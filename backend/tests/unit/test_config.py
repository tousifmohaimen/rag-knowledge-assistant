import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import Settings


def test_short_jwt_secret_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, jwt_secret=SecretStr("too-short"))


def test_production_flag() -> None:
    settings = Settings(_env_file=None, jwt_secret=SecretStr("x" * 32), environment="production")

    assert settings.is_production
