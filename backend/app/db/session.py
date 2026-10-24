"""Database engine and sessions.

Two kinds of session:

* ``get_session`` - not tied to a tenant. Used for login and other lookups that
  happen before we know who the caller belongs to.
* ``tenant_session`` - every transaction it opens first runs
  ``set_config('app.tenant_id', ...)``. The row-level security policies read that
  setting, so the database itself hides other tenants' rows.
"""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache

from sqlalchemy import event, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, SessionTransaction

from app.core.config import get_settings

TENANT_KEY = "tenant_id"


@lru_cache
def get_engine() -> AsyncEngine:
    return create_async_engine(get_settings().database_url, pool_pre_ping=True)


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def dispose_engine() -> None:
    if get_engine.cache_info().currsize:
        await get_engine().dispose()
    get_sessionmaker.cache_clear()
    get_engine.cache_clear()


@event.listens_for(Session, "after_begin")
def _apply_tenant(
    session: Session, transaction: SessionTransaction, connection: Connection
) -> None:
    # Runs at the start of every transaction, including the ones after a commit,
    # so the tenant can't be lost halfway through a request.
    tenant_id = session.info.get(TENANT_KEY)
    if tenant_id is not None:
        connection.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(tenant_id)},
        )


async def get_session() -> AsyncIterator[AsyncSession]:
    async with get_sessionmaker()() as session:
        yield session


@asynccontextmanager
async def tenant_session(tenant_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
    async with get_sessionmaker()(info={TENANT_KEY: tenant_id}) as session:
        yield session
