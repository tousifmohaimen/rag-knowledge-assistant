"""Hands work to the background worker.

The API only depends on the ``JobQueue`` protocol. Production uses Redis through arq;
tests swap in a queue that just records the jobs.
"""

import uuid
from typing import Protocol

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

INGEST_JOB = "ingest_document"


class QueueUnavailableError(Exception):
    pass


class JobQueue(Protocol):
    async def enqueue_ingest(self, tenant_id: uuid.UUID, document_id: uuid.UUID) -> None: ...

    async def close(self) -> None: ...


class ArqJobQueue:
    def __init__(self, redis_url: str) -> None:
        self._settings = RedisSettings.from_dsn(redis_url)
        self._pool: ArqRedis | None = None

    async def _get_pool(self) -> ArqRedis:
        # Connect on first use, so the API still starts (and serves everything else)
        # when Redis is briefly down.
        if self._pool is None:
            try:
                self._pool = await create_pool(self._settings)
            except (OSError, ConnectionError) as exc:
                raise QueueUnavailableError(str(exc)) from exc
        return self._pool

    async def enqueue_ingest(self, tenant_id: uuid.UUID, document_id: uuid.UUID) -> None:
        pool = await self._get_pool()
        try:
            await pool.enqueue_job(INGEST_JOB, str(tenant_id), str(document_id))
        except (OSError, ConnectionError) as exc:
            raise QueueUnavailableError(str(exc)) from exc

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.aclose()
            self._pool = None
