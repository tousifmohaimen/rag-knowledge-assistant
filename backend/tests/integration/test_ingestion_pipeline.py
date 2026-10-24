"""Runs the real pipeline (parse, chunk, embed, store) against Postgres.

Uses the offline hashing embedder, so no API key or network is needed.
"""

import uuid

from httpx import AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.api.deps import get_storage
from app.core.config import get_settings
from app.db.models import Chunk, Document, DocumentStatus
from app.ingestion.embeddings import EmbeddingError, HashingEmbedder
from app.ingestion.pipeline import GENERIC_ERROR, ingest_document
from tests.conftest import MakeUser, RecordingQueue, auth_headers
from tests.pdf_factory import make_pdf

HANDBOOK = make_pdf(
    [
        "# Leave policy\nStaff get 20 days of annual leave each year.",
        "## Sick leave\nStaff get 10 days of paid sick leave.\nFooter: Acme handbook",
        "## Parking\nParking is free in the basement.\nFooter: Acme handbook",
    ]
)


class FailingEmbedder(HashingEmbedder):
    async def embed(self, texts: object) -> list[list[float]]:
        raise EmbeddingError("embedding service is down")


async def _upload(client: AsyncClient, headers: dict[str, str], content: bytes, name: str) -> str:
    response = await client.post(
        "/api/v1/documents", headers=headers, files={"file": (name, content, "text/plain")}
    )
    assert response.status_code == 202, response.text
    return str(response.json()["id"])


async def _run_queued(queue: RecordingQueue, embedder: HashingEmbedder | None = None) -> None:
    for tenant_id, document_id in queue.ingest_jobs:
        await ingest_document(
            tenant_id,
            document_id,
            storage=get_storage(),
            embedder=embedder or HashingEmbedder(),
            settings=get_settings(),
        )
    queue.ingest_jobs.clear()


async def test_pdf_becomes_searchable_chunks_with_page_numbers(
    client: AsyncClient, make_user: MakeUser, queue: RecordingQueue, owner_engine: AsyncEngine
) -> None:
    headers = await auth_headers(client, await make_user())
    document_id = await _upload(client, headers, HANDBOOK, "handbook.pdf")

    await _run_queued(queue)

    document = (await client.get(f"/api/v1/documents/{document_id}", headers=headers)).json()
    assert document["status"] == "ready"
    assert document["page_count"] == 3
    assert document["error"] is None

    async with AsyncSession(owner_engine) as session:
        chunks = (
            await session.scalars(
                select(Chunk)
                .where(Chunk.document_id == uuid.UUID(document_id))
                .order_by(Chunk.chunk_index)
            )
        ).all()
        assert [c.page for c in chunks] == [1, 2, 3]
        assert chunks[1].section == "Sick leave"
        assert "10 days of paid sick leave" in chunks[1].text

        # The generated keyword-search column is filled in by Postgres.
        hit = await session.scalar(
            select(Chunk.page).where(
                Chunk.tsv.op("@@")(func.plainto_tsquery("english", "basement parking"))
            )
        )
        assert hit == 3


async def test_vector_search_finds_the_relevant_page(
    client: AsyncClient, make_user: MakeUser, queue: RecordingQueue, owner_engine: AsyncEngine
) -> None:
    headers = await auth_headers(client, await make_user())
    await _upload(client, headers, HANDBOOK, "handbook.pdf")
    await _run_queued(queue)
    (query_vector,) = await HashingEmbedder().embed(["how many sick leave days"])

    async with AsyncSession(owner_engine) as session:
        await session.execute(text("SET LOCAL hnsw.ef_search = 40"))
        best_page = await session.scalar(
            select(Chunk.page).order_by(Chunk.embedding.cosine_distance(query_vector)).limit(1)
        )

    assert best_page == 2


async def test_repeated_chunks_are_stored_once(
    client: AsyncClient, make_user: MakeUser, queue: RecordingQueue, owner_engine: AsyncEngine
) -> None:
    headers = await auth_headers(client, await make_user())
    repeated = make_pdf(["Same boilerplate text.", "Same boilerplate text.", "Unique page."])
    document_id = await _upload(client, headers, repeated, "repeated.pdf")

    await _run_queued(queue)

    async with AsyncSession(owner_engine) as session:
        texts = (
            await session.scalars(
                select(Chunk.text).where(Chunk.document_id == uuid.UUID(document_id))
            )
        ).all()
    assert sorted(texts) == ["Same boilerplate text.", "Unique page."]


async def test_running_twice_replaces_chunks_instead_of_duplicating(
    client: AsyncClient, make_user: MakeUser, queue: RecordingQueue, owner_engine: AsyncEngine
) -> None:
    headers = await auth_headers(client, await make_user())
    document_id = await _upload(client, headers, HANDBOOK, "handbook.pdf")
    await _run_queued(queue)

    reprocess = await client.post(f"/api/v1/documents/{document_id}/reprocess", headers=headers)
    assert reprocess.status_code == 202
    await _run_queued(queue)

    async with AsyncSession(owner_engine) as session:
        count = await session.scalar(
            select(func.count(Chunk.id)).where(Chunk.document_id == uuid.UUID(document_id))
        )
    assert count == 3


async def test_unreadable_file_is_marked_failed_with_a_helpful_message(
    client: AsyncClient, make_user: MakeUser, queue: RecordingQueue
) -> None:
    headers = await auth_headers(client, await make_user())
    document_id = await _upload(client, headers, make_pdf([""]), "scan.pdf")

    await _run_queued(queue)

    document = (await client.get(f"/api/v1/documents/{document_id}", headers=headers)).json()
    assert document["status"] == "failed"
    assert "OCR" in document["error"]


async def test_embedding_outage_marks_failed_without_leaking_details(
    client: AsyncClient, make_user: MakeUser, queue: RecordingQueue, owner_engine: AsyncEngine
) -> None:
    headers = await auth_headers(client, await make_user())
    document_id = await _upload(client, headers, b"Some notes about leave.", "notes.txt")

    await _run_queued(queue, embedder=FailingEmbedder())

    async with AsyncSession(owner_engine) as session:
        document = await session.get(Document, uuid.UUID(document_id))
        assert document is not None
        assert document.status == DocumentStatus.FAILED
        assert document.error == GENERIC_ERROR


async def test_document_deleted_before_processing_is_skipped(
    client: AsyncClient, make_user: MakeUser, queue: RecordingQueue
) -> None:
    headers = await auth_headers(client, await make_user())
    document_id = await _upload(client, headers, b"Short-lived notes.", "notes.txt")
    await client.delete(f"/api/v1/documents/{document_id}", headers=headers)

    tenant_id, queued_id = queue.ingest_jobs[0]
    result = await ingest_document(
        tenant_id,
        queued_id,
        storage=get_storage(),
        embedder=HashingEmbedder(),
        settings=get_settings(),
    )

    assert result is None
