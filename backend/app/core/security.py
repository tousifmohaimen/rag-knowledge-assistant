"""Password hashing and JSON Web Tokens."""

import enum
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.core.config import Settings

_hasher = PasswordHasher()

# Checked against when a login email doesn't exist, so a failed login takes about the
# same time either way. Without it, response time would reveal which emails are real.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def spend_verify_time(password: str) -> None:
    verify_password(password, _DUMMY_HASH)


def password_needs_rehash(password_hash: str) -> bool:
    """True when the hash was made with older, weaker settings than the current ones."""
    return _hasher.check_needs_rehash(password_hash)


class TokenType(enum.StrEnum):
    ACCESS = "access"
    REFRESH = "refresh"


class TokenError(Exception):
    """The token is missing, expired, tampered with, or the wrong type."""


@dataclass(frozen=True, slots=True)
class TokenClaims:
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    role: str
    token_type: TokenType
    expires_at: datetime


def create_token(
    *,
    user_id: uuid.UUID,
    tenant_id: uuid.UUID,
    role: str,
    token_type: TokenType,
    settings: Settings,
    now: datetime | None = None,
) -> tuple[str, datetime]:
    issued_at = now or datetime.now(UTC)
    lifetime = (
        timedelta(minutes=settings.access_token_minutes)
        if token_type is TokenType.ACCESS
        else timedelta(days=settings.refresh_token_days)
    )
    expires_at = issued_at + lifetime
    claims = {
        "sub": str(user_id),
        "tid": str(tenant_id),
        "role": role,
        "type": token_type.value,
        "iat": issued_at,
        "exp": expires_at,
        # Unique token ID, so a single token can be revoked later.
        "jti": uuid.uuid4().hex,
    }
    token = jwt.encode(
        claims, settings.jwt_secret.get_secret_value(), algorithm=settings.jwt_algorithm
    )
    return token, expires_at


def decode_token(token: str, *, expected_type: TokenType, settings: Settings) -> TokenClaims:
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
            options={"require": ["sub", "tid", "type", "exp", "iat"]},
        )
        if claims["type"] != expected_type.value:
            raise TokenError("wrong token type")
        return TokenClaims(
            user_id=uuid.UUID(claims["sub"]),
            tenant_id=uuid.UUID(claims["tid"]),
            role=str(claims.get("role", "")),
            token_type=expected_type,
            expires_at=datetime.fromtimestamp(claims["exp"], UTC),
        )
    except (jwt.PyJWTError, ValueError, KeyError, TypeError) as exc:
        raise TokenError(str(exc)) from exc
