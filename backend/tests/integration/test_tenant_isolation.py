"""Proves the second lock: Postgres itself hides other tenants' rows.

These queries deliberately have NO ``WHERE tenant_id = ...`` filter. If row-level
security were missing, they would see every tenant's documents.
"""

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.db.models import Document, Tenant
from app.db.session import get_sessionmaker, tenant_session


async def _tenant_with_document(owner_engine: AsyncEngine, name: str) -> uuid.UUID:
    async with AsyncSession(owner_engine, expire_on_commit=False) as session:
        tenant = Tenant(name=name)
        session.add(tenant)
        await session.flush()
        session.add(_document(tenant.id, f"{name}-handbook.pdf"))
        await session.commit()
        return tenant.id


def _document(tenant_id: uuid.UUID, filename: str) -> Document:
    return Document(
        tenant_id=tenant_id,
        filename=filename,
        content_type="application/pdf",
        size_bytes=1024,
        content_hash=uuid.uuid4().hex,
    )


async def test_tenant_sees_only_its_own_documents(owner_engine: AsyncEngine) -> None:
    acme = await _tenant_with_document(owner_engine, "acme")
    await _tenant_with_document(owner_engine, "globex")

    async with tenant_session(acme) as session:
        filenames = (await session.scalars(select(Document.filename))).all()

    assert filenames == ["acme-handbook.pdf"]


async def test_tenant_is_still_applied_after_a_commit(owner_engine: AsyncEngine) -> None:
    acme = await _tenant_with_document(owner_engine, "acme")
    await _tenant_with_document(owner_engine, "globex")

    async with tenant_session(acme) as session:
        await session.scalar(select(func.count(Document.id)))
        await session.commit()
        count_after_commit = await session.scalar(select(func.count(Document.id)))

    assert count_after_commit == 1


async def test_without_a_tenant_no_rows_are_visible(owner_engine: AsyncEngine) -> None:
    await _tenant_with_document(owner_engine, "acme")

    async with get_sessionmaker()() as session:
        count = await session.scalar(select(func.count(Document.id)))

    assert count == 0


async def test_tenant_cannot_write_rows_for_another_tenant(owner_engine: AsyncEngine) -> None:
    acme = await _tenant_with_document(owner_engine, "acme")
    globex = await _tenant_with_document(owner_engine, "globex")

    async with tenant_session(acme) as session:
        session.add(_document(globex, "planted.pdf"))
        with pytest.raises(DBAPIError, match="row-level security"):
            await session.commit()
