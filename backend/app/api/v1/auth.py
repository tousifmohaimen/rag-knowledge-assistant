from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser, SessionDep, SettingsDep
from app.core.security import TokenError
from app.db.models import User
from app.schemas.auth import LoginRequest, RefreshRequest, TokenPair, UserOut
from app.services.auth import (
    InvalidCredentialsError,
    authenticate,
    issue_tokens,
    user_from_refresh_token,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login")
async def login(body: LoginRequest, session: SessionDep, settings: SettingsDep) -> TokenPair:
    try:
        user = await authenticate(session, body.email, body.password)
    except InvalidCredentialsError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password"
        ) from None
    return issue_tokens(user, settings)


@router.post("/refresh")
async def refresh(body: RefreshRequest, session: SessionDep, settings: SettingsDep) -> TokenPair:
    try:
        user = await user_from_refresh_token(session, body.refresh_token, settings)
    except (TokenError, InvalidCredentialsError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token"
        ) from None
    return issue_tokens(user, settings)


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> User:
    return user
