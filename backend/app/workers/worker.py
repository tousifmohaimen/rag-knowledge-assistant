"""Background worker. Start it with ``arq app.workers.worker.WorkerSettings``."""

import logging
import uuid
from typing import Any, ClassVar

from arq import func
from arq.connections import RedisSettings

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.storage import LocalStorage
from app.db.session import dispose_engine
from app.ingestion.embeddings import build_embedder
from app.ingestion.pipeline import GENERIC_ERROR, ingest_document, mark_failed
from app.workers.queue import INGEST_JOB

logger = logging.getLogger(__name__)


async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    ctx["settings"] = settings
    ctx["storage"] = LocalStorage(settings.storage_dir)
    ctx["embedder"] = build_embedder(settings)
    logger.info("worker started with embedding provider %r", settings.embedding_provider)


async def shutdown(ctx: dict[str, Any]) -> None:
    await ctx["embedder"].aclose()
    await dispose_engine()


async def ingest_document_job(ctx: dict[str, Any], tenant_id: str, document_id: str) -> str:
    tenant, document = uuid.UUID(tenant_id), uuid.UUID(document_id)
    try:
        status = await ingest_document(
            tenant,
            document,
            storage=ctx["storage"],
            embedder=ctx["embedder"],
            settings=ctx["settings"],
        )
    except Exception:
        # Anything unexpected: record it so the document doesn't sit in "processing".
        logger.exception("unexpected error while ingesting document %s", document_id)
        await mark_failed(tenant, document, GENERIC_ERROR)
        return "failed"
    return status.value if status else "deleted"


class WorkerSettings:
    # Registered under the same name the API enqueues, so the two can't drift apart.
    functions: ClassVar[list[Any]] = [func(ingest_document_job, name=INGEST_JOB)]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    max_jobs = 4
    job_timeout = 600
    keep_result = 3600
