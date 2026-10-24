"""Login and token refresh.

Password hashing is deliberately slow (that's what makes it safe), so it runs in a
thread pool instead of blocking the event loop that serves every other request.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core.config import Settings
from app.core.security import (
    TokenType,
    create_token,
    decode_token,
    hash_password,
    password_needs_rehash,
    spend_verify_time,
    verify_password,
)
from app.db.models import User
from app.schemas.auth import TokenPair


class InvalidCredentialsError(Exception):
    """Wrong email or password, or the account is disabled.

    One error for all three cases, so an attacker can't learn which emails exist.
    """


async def authenticate(session: AsyncSession, email: str, password: str) -> User:
    user = await session.scalar(select(User).where(User.email == email.lower()))
    if user is None:
        await run_in_threadpool(spend_verify_time, password)
        raise InvalidCredentialsError

    password_ok = await run_in_threadpool(verify_password, password, user.password_hash)
    if not password_ok or not user.is_active:
        raise InvalidCredentialsError

    # Upgrade old hashes quietly the next time the user logs in.
    if password_needs_rehash(user.password_hash):
        user.password_hash = await run_in_threadpool(hash_password, password)
        await session.commit()
    return user


async def user_from_refresh_token(session: AsyncSession, token: str, settings: Settings) -> User:
    """Raises ``TokenError`` for a bad token and ``InvalidCredentialsError`` for a bad user."""
    claims = decode_token(token, expected_type=TokenType.REFRESH, settings=settings)
    user = await session.get(User, claims.user_id)
    if user is None or not user.is_active or user.tenant_id != claims.tenant_id:
        raise InvalidCredentialsError
    return user


def issue_tokens(user: User, settings: Settings) -> TokenPair:
    access_token, _ = create_token(
        user_id=user.id,
        tenant_id=user.tenant_id,
        role=user.role,
        token_type=TokenType.ACCESS,
        settings=settings,
    )
    refresh_token, _ = create_token(
        user_id=user.id,
        tenant_id=user.tenant_id,
        role=user.role,
        token_type=TokenType.REFRESH,
        settings=settings,
    )
    return TokenPair(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.access_token_minutes * 60,
    )
