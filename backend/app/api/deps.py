"""Shared FastAPI dependencies: settings, database sessions, storage, the job queue
and the current user."""

from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Annotated, cast

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.security import TokenError, TokenType, decode_token
from app.core.storage import LocalStorage, Storage
from app.db.models import User, UserRole
from app.db.session import get_session, tenant_session
from app.workers.queue import JobQueue

SettingsDep = Annotated[Settings, Depends(get_settings)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]


@lru_cache
def get_storage() -> Storage:
    return LocalStorage(get_settings().storage_dir)


def get_job_queue(request: Request) -> JobQueue:
    return cast(JobQueue, request.app.state.job_queue)


StorageDep = Annotated[Storage, Depends(get_storage)]
JobQueueDep = Annotated[JobQueue, Depends(get_job_queue)]

_bearer = HTTPBearer(auto_error=False)


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_user(
    session: SessionDep,
    settings: SettingsDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    if credentials is None:
        raise _unauthorized()
    try:
        claims = decode_token(
            credentials.credentials, expected_type=TokenType.ACCESS, settings=settings
        )
    except TokenError:
        raise _unauthorized() from None

    # Load the user on every request so a disabled account loses access immediately,
    # instead of keeping it until its token expires.
    user = await session.get(User, claims.user_id)
    if user is None or not user.is_active or user.tenant_id != claims.tenant_id:
        raise _unauthorized()
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def require_admin(user: CurrentUser) -> User:
    if user.role != UserRole.ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admins only")
    return user


AdminUser = Annotated[User, Depends(require_admin)]


async def get_tenant_session(user: CurrentUser) -> AsyncIterator[AsyncSession]:
    """A session where Postgres only shows rows belonging to the caller's tenant."""
    async with tenant_session(user.tenant_id) as session:
        yield session


TenantSessionDep = Annotated[AsyncSession, Depends(get_tenant_session)]
