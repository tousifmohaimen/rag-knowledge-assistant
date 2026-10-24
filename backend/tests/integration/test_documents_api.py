import uuid
from typing import Any

import pytest
from httpx import AsyncClient

from app.core.config import get_settings
from app.core.storage import LocalStorage, document_key
from app.db.models import UserRole
from tests.conftest import MakeUser, RecordingQueue, auth_headers
from tests.pdf_factory import make_pdf

PDF_BYTES = make_pdf(["# Leave policy\nStaff get 20 days of annual leave."])


async def _upload(
    client: AsyncClient,
    headers: dict[str, str],
    content: bytes = PDF_BYTES,
    filename: str = "handbook.pdf",
) -> tuple[int, dict[str, Any]]:
    response = await client.post(
        "/api/v1/documents",
        headers=headers,
        files={"file": (filename, content, "application/octet-stream")},
    )
    return response.status_code, response.json()


async def test_admin_upload_is_stored_and_queued(
    client: AsyncClient, make_user: MakeUser, queue: RecordingQueue
) -> None:
    admin = await make_user()
    headers = await auth_headers(client, admin)

    status, body = await _upload(client, headers)

    assert status == 202
    assert body["status"] == "queued"
    assert body["content_type"] == "application/pdf"
    assert body["filename"] == "handbook.pdf"
    assert body["size_bytes"] == len(PDF_BYTES)
    document_id = uuid.UUID(str(body["id"]))
    assert queue.ingest_jobs == [(admin.tenant_id, document_id)]
    storage = LocalStorage(get_settings().storage_dir)
    assert await storage.read(document_key(admin.tenant_id, document_id)) == PDF_BYTES


async def test_same_file_twice_is_rejected_with_the_existing_id(
    client: AsyncClient, make_user: MakeUser
) -> None:
    headers = await auth_headers(client, await make_user())
    _, first = await _upload(client, headers)

    status, body = await _upload(client, headers, filename="copy.pdf")

    assert status == 409
    assert body["detail"]["document_id"] == first["id"]


async def test_two_companies_can_upload_the_same_file(
    client: AsyncClient, make_user: MakeUser
) -> None:
    acme = await auth_headers(client, await make_user("admin@acme.com"))
    globex = await auth_headers(client, await make_user("admin@globex.com"))

    assert (await _upload(client, acme))[0] == 202
    assert (await _upload(client, globex))[0] == 202


@pytest.mark.parametrize(
    ("content", "filename", "expected_status"),
    [
        (b"MZ\x90\x00 not a document", "setup.exe", 415),
        (b"plain text", "notes.docx", 415),
        (b"", "empty.txt", 400),
    ],
)
async def test_invalid_uploads_are_rejected(
    client: AsyncClient,
    make_user: MakeUser,
    queue: RecordingQueue,
    content: bytes,
    filename: str,
    expected_status: int,
) -> None:
    headers = await auth_headers(client, await make_user())

    status, _ = await _upload(client, headers, content, filename)

    assert status == expected_status
    assert queue.ingest_jobs == []


async def test_oversized_upload_is_rejected(
    client: AsyncClient, make_user: MakeUser, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await auth_headers(client, await make_user())
    monkeypatch.setattr(get_settings(), "max_upload_mb", 1)

    status, _ = await _upload(client, headers, b"x" * (2 * 1024 * 1024), "big.txt")

    assert status == 413


async def test_members_can_list_but_not_upload(client: AsyncClient, make_user: MakeUser) -> None:
    admin = await make_user()
    member = await make_user("member@acme.com", role=UserRole.MEMBER, tenant_id=admin.tenant_id)
    await _upload(client, await auth_headers(client, admin))
    member_headers = await auth_headers(client, member)

    status, _ = await _upload(client, member_headers, b"notes", "notes.txt")
    listing = await client.get("/api/v1/documents", headers=member_headers)

    assert status == 403
    assert listing.status_code == 200
    assert listing.json()["total"] == 1


async def test_listing_shows_only_your_companys_documents(
    client: AsyncClient, make_user: MakeUser
) -> None:
    acme = await auth_headers(client, await make_user("admin@acme.com"))
    globex = await auth_headers(client, await make_user("admin@globex.com"))
    await _upload(client, acme)
    await _upload(client, globex, b"Globex secret plan", "plan.txt")

    body = (await client.get("/api/v1/documents", headers=acme)).json()

    assert body["total"] == 1
    assert [d["filename"] for d in body["items"]] == ["handbook.pdf"]


async def test_another_companys_document_is_not_found(
    client: AsyncClient, make_user: MakeUser
) -> None:
    acme = await auth_headers(client, await make_user("admin@acme.com"))
    globex = await auth_headers(client, await make_user("admin@globex.com"))
    _, document = await _upload(client, globex)

    get = await client.get(f"/api/v1/documents/{document['id']}", headers=acme)
    delete = await client.delete(f"/api/v1/documents/{document['id']}", headers=acme)

    assert get.status_code == delete.status_code == 404


async def test_delete_removes_the_record_and_the_file(
    client: AsyncClient, make_user: MakeUser
) -> None:
    admin = await make_user()
    headers = await auth_headers(client, admin)
    _, document = await _upload(client, headers)
    document_id = uuid.UUID(str(document["id"]))

    response = await client.delete(f"/api/v1/documents/{document_id}", headers=headers)

    assert response.status_code == 204
    missing = await client.get(f"/api/v1/documents/{document_id}", headers=headers)
    assert missing.status_code == 404
    storage = LocalStorage(get_settings().storage_dir)
    assert not (storage.root / document_key(admin.tenant_id, document_id)).exists()


async def test_reprocess_is_refused_while_processing(
    client: AsyncClient, make_user: MakeUser
) -> None:
    headers = await auth_headers(client, await make_user())
    _, document = await _upload(client, headers)

    response = await client.post(f"/api/v1/documents/{document['id']}/reprocess", headers=headers)

    assert response.status_code == 409
