import uuid
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, Query, Request, Response, UploadFile, status
from sqlalchemy import func, select

from app.api.deps import (
    AdminUser,
    CurrentUser,
    JobQueueDep,
    SettingsDep,
    StorageDep,
    TenantSessionDep,
)
from app.core.storage import document_key
from app.db.models import Document, DocumentStatus
from app.schemas.documents import DocumentList, DocumentOut
from app.services.documents import (
    DuplicateDocumentError,
    UploadRejectedError,
    accept_upload,
    requeue,
)

router = APIRouter(prefix="/documents", tags=["documents"])

# Multipart adds a little overhead around the file itself.
_MULTIPART_OVERHEAD = 64 * 1024


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=DocumentOut)
async def upload_document(
    request: Request,
    file: Annotated[UploadFile, File(description="A PDF, .txt or .md file")],
    user: AdminUser,
    session: TenantSessionDep,
    storage: StorageDep,
    queue: JobQueueDep,
    settings: SettingsDep,
) -> Document:
    """Upload a document. It is processed in the background; poll it until it is ready."""
    declared = request.headers.get("content-length")
    if (
        declared
        and declared.isdigit()
        and int(declared) > settings.max_upload_bytes + _MULTIPART_OVERHEAD
    ):
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"The file is larger than {settings.max_upload_mb} MB.",
        )
    try:
        return await accept_upload(
            session,
            tenant_id=user.tenant_id,
            upload=file,
            storage=storage,
            queue=queue,
            max_bytes=settings.max_upload_bytes,
        )
    except UploadRejectedError as exc:
        raise HTTPException(exc.status_code, exc.message) from None
    except DuplicateDocumentError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "message": "This file has already been uploaded.",
                "document_id": str(exc.existing_id) if exc.existing_id else None,
            },
        ) from None


@router.get("")
async def list_documents(
    _: CurrentUser,
    session: TenantSessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DocumentList:
    # No tenant filter needed here: row-level security only returns this tenant's rows.
    total = await session.scalar(select(func.count(Document.id))) or 0
    documents = await session.scalars(
        select(Document)
        .order_by(Document.created_at.desc(), Document.id)
        .limit(limit)
        .offset(offset)
    )
    return DocumentList(
        items=[DocumentOut.model_validate(d) for d in documents],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{document_id}", response_model=DocumentOut)
async def get_document(
    document_id: uuid.UUID, _: CurrentUser, session: TenantSessionDep
) -> Document:
    return await _get_or_404(session, document_id)


@router.post(
    "/{document_id}/reprocess", status_code=status.HTTP_202_ACCEPTED, response_model=DocumentOut
)
async def reprocess_document(
    document_id: uuid.UUID, _: AdminUser, session: TenantSessionDep, queue: JobQueueDep
) -> Document:
    """Run ingestion again, for example after a failure or a change in chunk settings."""
    document = await _get_or_404(session, document_id)
    if document.status in (DocumentStatus.QUEUED, DocumentStatus.PROCESSING):
        raise HTTPException(status.HTTP_409_CONFLICT, "The document is already being processed.")
    return await requeue(session, document, queue=queue)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: uuid.UUID, user: AdminUser, session: TenantSessionDep, storage: StorageDep
) -> Response:
    """Delete a document, its chunks and its vectors, and the stored file."""
    document = await _get_or_404(session, document_id)
    await session.delete(document)  # chunks are removed by ON DELETE CASCADE
    await session.commit()
    await storage.delete(document_key(user.tenant_id, document_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _get_or_404(session: TenantSessionDep, document_id: uuid.UUID) -> Document:
    document = await session.get(Document, document_id)
    if document is None:
        # Another tenant's document looks exactly like one that doesn't exist.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    return document
