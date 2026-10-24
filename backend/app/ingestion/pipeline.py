"""The ingestion pipeline: stored file -> pages -> chunks -> vectors -> database.

Runs in the background worker. It is safe to run twice for the same document (a retry
or a "reprocess" request): existing chunks are replaced in the same transaction that
marks the document ready, so searches never see a half-indexed document.
"""

import asyncio
import logging
import time
import uuid

from sqlalchemy import delete

from app.core.config import Settings
from app.core.storage import Storage, StorageError, document_key
from app.db.models import Chunk, Document, DocumentStatus
from app.db.session import tenant_session
from app.ingestion.chunking import ChunkDraft, chunk_pages
from app.ingestion.embeddings import Embedder, EmbeddingError
from app.ingestion.parsing import ParseError, parse_document

logger = logging.getLogger(__name__)

# Shown to users when something unexpected fails; details go to the logs only.
GENERIC_ERROR = "Processing failed because of an internal error. Try reprocessing it."


async def ingest_document(
    tenant_id: uuid.UUID,
    document_id: uuid.UUID,
    *,
    storage: Storage,
    embedder: Embedder,
    settings: Settings,
) -> DocumentStatus | None:
    """Process one document. Returns its final status, or None if it no longer exists."""
    started = time.perf_counter()
    async with tenant_session(tenant_id) as session:
        document = await session.get(Document, document_id)
        if document is None:
            logger.info("document %s was deleted before processing", document_id)
            return None
        document.status = DocumentStatus.PROCESSING
        document.error = None
        content_type = document.content_type
        await session.commit()

    try:
        data = await storage.read(document_key(tenant_id, document_id))
        # Parsing is CPU-bound, so it runs in a thread to keep the event loop free.
        pages = await asyncio.to_thread(parse_document, data, content_type)
        drafts = _unique(
            chunk_pages(
                pages,
                target_tokens=settings.chunk_target_tokens,
                overlap_tokens=settings.chunk_overlap_tokens,
            )
        )
        vectors = await embedder.embed([draft.text for draft in drafts])
    except ParseError as exc:
        return await mark_failed(tenant_id, document_id, str(exc))
    except (EmbeddingError, StorageError):
        logger.exception("ingestion failed for document %s", document_id)
        return await mark_failed(tenant_id, document_id, GENERIC_ERROR)

    async with tenant_session(tenant_id) as session:
        document = await session.get(Document, document_id)
        if document is None:
            return None
        await session.execute(delete(Chunk).where(Chunk.document_id == document_id))
        session.add_all(
            Chunk(
                tenant_id=tenant_id,
                document_id=document_id,
                chunk_index=index,
                page=draft.page,
                section=draft.section,
                text=draft.text,
                token_count=draft.token_count,
                chunk_hash=draft.chunk_hash,
                embedding=vector,
            )
            for index, (draft, vector) in enumerate(zip(drafts, vectors, strict=True))
        )
        document.status = DocumentStatus.READY
        document.page_count = len(pages)
        await session.commit()

    logger.info(
        "document %s ready: %d pages, %d chunks in %.1fs",
        document_id,
        len(pages),
        len(drafts),
        time.perf_counter() - started,
    )
    return DocumentStatus.READY


def _unique(drafts: list[ChunkDraft]) -> list[ChunkDraft]:
    """Drop repeated chunks (headers, footers, copied boilerplate) inside a document."""
    seen: set[str] = set()
    unique: list[ChunkDraft] = []
    for draft in drafts:
        if draft.chunk_hash not in seen:
            seen.add(draft.chunk_hash)
            unique.append(draft)
    return unique


async def mark_failed(tenant_id: uuid.UUID, document_id: uuid.UUID, message: str) -> DocumentStatus:
    async with tenant_session(tenant_id) as session:
        document = await session.get(Document, document_id)
        if document is not None:
            document.status = DocumentStatus.FAILED
            document.error = message
            await session.commit()
    logger.info("document %s failed: %s", document_id, message)
    return DocumentStatus.FAILED
