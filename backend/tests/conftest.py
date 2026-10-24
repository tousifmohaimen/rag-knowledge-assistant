"""Test setup.

Integration tests run against a real Postgres (with pgvector), because the things
most worth testing here - row-level security, generated columns, vector indexes -
don't exist in SQLite or in mocks.
"""

import os
import tempfile

# Settings are read when the app is imported, so these must be set first.
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("JWT_SECRET", "test-secret-that-is-at-least-32-characters-long")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://rka_app:rka_app@localhost:5432/rka_test",
)
os.environ.setdefault(
    "MIGRATIONS_DATABASE_URL",
    "postgresql+asyncpg://rka:rka@localhost:5432/rka_test",
)
os.environ.setdefault("STORAGE_DIR", tempfile.mkdtemp(prefix="rka-test-uploads-"))
os.environ.setdefault("EMBEDDING_PROVIDER", "fake")

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from app.api.deps import get_job_queue
from app.core.security import hash_password
from app.db.models import Tenant, User, UserRole
from app.main import create_app

BACKEND_DIR = Path(__file__).resolve().parent.parent


def _assert_test_database(url: str) -> None:
    # The suite wipes every table. Refuse to run against anything but a test database.
    database = make_url(url).database or ""
    if not database.endswith("_test"):
        raise RuntimeError(f"Refusing to run tests against database {database!r}")


@pytest.fixture(scope="session", autouse=True)
def _migrated_database() -> Iterator[None]:
    _assert_test_database(os.environ["MIGRATIONS_DATABASE_URL"])
    _assert_test_database(os.environ["DATABASE_URL"])
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    # Going down and back up also proves the downgrade works.
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    yield


@pytest.fixture(scope="session")
async def owner_engine() -> AsyncIterator[AsyncEngine]:
    """Connects as the schema owner (a superuser locally), which bypasses row-level security.

    Used only to set up and clean test data.
    """
    engine = create_async_engine(os.environ["MIGRATIONS_DATABASE_URL"])
    yield engine
    await engine.dispose()


@pytest.fixture(autouse=True)
async def _clean_database(owner_engine: AsyncEngine) -> None:
    async with owner_engine.begin() as conn:
        await conn.execute(text("TRUNCATE tenants CASCADE"))


class RecordingQueue:
    """Stands in for Redis: remembers what would have been queued."""

    def __init__(self) -> None:
        self.ingest_jobs: list[tuple[uuid.UUID, uuid.UUID]] = []

    async def enqueue_ingest(self, tenant_id: uuid.UUID, document_id: uuid.UUID) -> None:
        self.ingest_jobs.append((tenant_id, document_id))

    async def close(self) -> None:
        return None


@pytest.fixture
def queue() -> RecordingQueue:
    return RecordingQueue()


@pytest.fixture
async def client(queue: RecordingQueue) -> AsyncIterator[AsyncClient]:
    app = create_app()
    app.dependency_overrides[get_job_queue] = lambda: queue
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


async def auth_headers(client: AsyncClient, user: "SeededUser") -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/login", json={"email": user.email, "password": user.password}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@dataclass(frozen=True)
class SeededUser:
    id: uuid.UUID
    tenant_id: uuid.UUID
    email: str
    password: str


MakeUser = Callable[..., Awaitable[SeededUser]]


@pytest.fixture
def make_user(owner_engine: AsyncEngine) -> MakeUser:
    async def _make_user(
        email: str = "admin@acme.com",
        password: str = "correct horse battery staple",
        *,
        role: UserRole = UserRole.ADMIN,
        is_active: bool = True,
        tenant_id: uuid.UUID | None = None,
    ) -> SeededUser:
        async with AsyncSession(owner_engine, expire_on_commit=False) as session:
            if tenant_id is None:
                tenant = Tenant(name="Acme")
                session.add(tenant)
                await session.flush()
                tenant_id = tenant.id
            user = User(
                tenant_id=tenant_id,
                email=email,
                password_hash=hash_password(password),
                role=role,
                is_active=is_active,
            )
            session.add(user)
            await session.commit()
            return SeededUser(id=user.id, tenant_id=tenant_id, email=email, password=password)

    return _make_user
