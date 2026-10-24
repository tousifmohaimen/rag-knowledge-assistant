import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.core.config import get_settings
from app.core.security import (
    TokenError,
    TokenType,
    create_token,
    decode_token,
    hash_password,
    verify_password,
)


def test_password_hash_verifies_only_the_right_password() -> None:
    password_hash = hash_password("s3cret-password")

    assert password_hash != "s3cret-password"
    assert verify_password("s3cret-password", password_hash)
    assert not verify_password("wrong-password", password_hash)


def test_verify_password_rejects_a_malformed_hash() -> None:
    assert not verify_password("anything", "not-a-real-hash")


def _token(token_type: TokenType, now: datetime | None = None) -> tuple[str, uuid.UUID, uuid.UUID]:
    user_id, tenant_id = uuid.uuid4(), uuid.uuid4()
    token, _ = create_token(
        user_id=user_id,
        tenant_id=tenant_id,
        role="admin",
        token_type=token_type,
        settings=get_settings(),
        now=now,
    )
    return token, user_id, tenant_id


def test_access_token_round_trip() -> None:
    token, user_id, tenant_id = _token(TokenType.ACCESS)

    claims = decode_token(token, expected_type=TokenType.ACCESS, settings=get_settings())

    assert claims.user_id == user_id
    assert claims.tenant_id == tenant_id
    assert claims.role == "admin"


def test_refresh_token_is_not_accepted_as_access_token() -> None:
    token, _, _ = _token(TokenType.REFRESH)

    with pytest.raises(TokenError):
        decode_token(token, expected_type=TokenType.ACCESS, settings=get_settings())


def test_expired_token_is_rejected() -> None:
    long_ago = datetime.now(UTC) - timedelta(days=1)
    token, _, _ = _token(TokenType.ACCESS, now=long_ago)

    with pytest.raises(TokenError):
        decode_token(token, expected_type=TokenType.ACCESS, settings=get_settings())


def test_token_signed_with_another_secret_is_rejected() -> None:
    forged = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "tid": str(uuid.uuid4()),
            "type": "access",
            "iat": datetime.now(UTC),
            "exp": datetime.now(UTC) + timedelta(minutes=5),
        },
        "a-different-secret-that-is-also-long-enough",
        algorithm="HS256",
    )

    with pytest.raises(TokenError):
        decode_token(forged, expected_type=TokenType.ACCESS, settings=get_settings())
