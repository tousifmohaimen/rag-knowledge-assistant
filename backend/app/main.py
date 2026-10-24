import logging
import re
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.logging import configure_logging, request_id_var
from app.db.session import dispose_engine
from app.workers.queue import ArqJobQueue

logger = logging.getLogger("app.request")

# Accept a caller's request ID only if it looks safe to write into logs.
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    await app.state.job_queue.close()
    await dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        lifespan=lifespan,
        # Interactive API docs are handy in development but not exposed in production.
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else "/openapi.json",
    )
    # Connects to Redis on first use, so the API starts even if Redis isn't up yet.
    app.state.job_queue = ArqJobQueue(settings.redis_url)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        incoming = request.headers.get("x-request-id", "")
        request_id = incoming if _VALID_REQUEST_ID.match(incoming) else uuid.uuid4().hex
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            logger.info("request", extra={"http": _http(request, response.status_code, started)})
            return response
        except Exception:
            logger.exception("unhandled error", extra={"http": _http(request, 500, started)})
            raise
        finally:
            request_id_var.reset(token)

    app.include_router(api_router, prefix="/api/v1")
    return app


def _http(request: Request, status_code: int, started: float) -> dict[str, object]:
    return {
        "method": request.method,
        "path": request.url.path,
        "status": status_code,
        "duration_ms": round((time.perf_counter() - started) * 1000, 1),
    }


app = create_app()
