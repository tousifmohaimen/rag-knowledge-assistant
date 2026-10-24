"""Accepting uploads: validate, store, record, queue."""

import hashlib
import logging
import re
import unicodedata
import uuid
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core.storage import Storage, document_key
from app.db.models import Document, DocumentStatus
from app.ingestion.parsing import detect_content_type
from app.workers.queue import JobQueue, QueueUnavailableError

logger = logging.getLogger(__name__)

_READ_SIZE = 1024 * 1024
_HEAD_SIZE = 8 * 1024
_UNSAFE_FILENAME = re.compile(r"[\x00-\x1f\x7f/\\]")


class UploadRejectedError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


class DuplicateDocumentError(Exception):
    def __init__(self, existing_id: uuid.UUID | None) -> None:
        super().__init__(f"duplicate of document {existing_id}")
        self.existing_id = existing_id


def clean_filename(raw: str | None) -> str:
    """Keep the name for display only; it is never used as a path."""
    name = unicodedata.normalize("NFC", raw or "")
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    name = _UNSAFE_FILENAME.sub("", name).strip()
    return name[:255] or "untitled"


async def accept_upload(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    upload: UploadFile,
    storage: Storage,
    queue: JobQueue,
    max_bytes: int,
) -> Document:
    filename = clean_filename(upload.filename)
    temp = storage.temp_path()
    promoted_key: str | None = None
    try:
        size, digest, head = await _copy_to_temp(upload, temp, max_bytes)
        if size == 0:
            raise UploadRejectedError(400, "The file is empty.")
        content_type = detect_content_type(head, filename)
        if content_type is None:
            raise UploadRejectedError(415, "Only PDF, .txt and .md files are supported.")

        existing = await session.scalar(
            select(Document.id).where(
                Document.tenant_id == tenant_id, Document.content_hash == digest
            )
        )
        if existing is not None:
            raise DuplicateDocumentError(existing)

        document = Document(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            filename=filename,
            content_type=content_type,
            size_bytes=size,
            content_hash=digest,
            status=DocumentStatus.QUEUED,
        )
        session.add(document)
        try:
            await session.flush()
        except IntegrityError:
            # Two uploads of the same file at the same moment: the database caught it.
            await session.rollback()
            existing = await session.scalar(
                select(Document.id).where(
                    Document.tenant_id == tenant_id, Document.content_hash == digest
                )
            )
            raise DuplicateDocumentError(existing) from None

        key = document_key(tenant_id, document.id)
        await storage.promote(temp, key)
        promoted_key = key
        await session.commit()
    except BaseException:
        # Leave no orphan files behind, whatever went wrong.
        await storage.discard(temp)
        if promoted_key is not None:
            await storage.delete(promoted_key)
        raise

    # Queue only after the commit, so the worker can always find the row.
    try:
        await queue.enqueue_ingest(tenant_id, document.id)
    except QueueUnavailableError:
        logger.exception("could not queue document %s", document.id)
        document.status = DocumentStatus.FAILED
        document.error = "Processing could not be started. Try reprocessing it."
        await session.commit()
    return document


async def requeue(session: AsyncSession, document: Document, *, queue: JobQueue) -> Document:
    document.status = DocumentStatus.QUEUED
    document.error = None
    await session.commit()
    try:
        await queue.enqueue_ingest(document.tenant_id, document.id)
    except QueueUnavailableError:
        logger.exception("could not queue document %s", document.id)
        document.status = DocumentStatus.FAILED
        document.error = "Processing could not be started. Try reprocessing it."
        await session.commit()
    return document


async def _copy_to_temp(upload: UploadFile, temp: Path, max_bytes: int) -> tuple[int, str, bytes]:
    """Stream the upload to disk while hashing it and enforcing the size limit."""
    hasher = hashlib.sha256()
    size = 0
    head = b""
    with temp.open("wb") as out:
        while chunk := await upload.read(_READ_SIZE):
            size += len(chunk)
            if size > max_bytes:
                raise UploadRejectedError(
                    413, f"The file is larger than {max_bytes // (1024 * 1024)} MB."
                )
            if len(head) < _HEAD_SIZE:
                head += chunk[: _HEAD_SIZE - len(head)]
            hasher.update(chunk)
            await run_in_threadpool(out.write, chunk)
    return size, hasher.hexdigest(), head
