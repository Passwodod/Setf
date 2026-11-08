import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import func, select

from app.api.deps import CurrentUser, Session
from app.core.config import get_settings
from app.core.ratelimit import client_ip, enforce_rate_limit
from app.models import User
from app.models.base import Role
from app.schemas.auth import LoginRequest, RefreshRequest, RegisterRequest, TokenPair, UserOut
from app.security.passwords import DUMMY_HASH, hash_password, verify_password
from app.security.tokens import TokenError, create_token, decode_token
from app.services.audit import audit

router = APIRouter(prefix="/auth", tags=["auth"])


def _pair(user: User) -> TokenPair:
    return TokenPair(
        access_token=create_token(user.id, "access", token_version=user.token_version),
        refresh_token=create_token(user.id, "refresh", token_version=user.token_version),
    )


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest, request: Request, session: Session) -> User:
    settings = get_settings()
    await enforce_rate_limit("register", client_ip(request), 5)
    # Serialise bootstrap so two concurrent first-registrations cannot both become admin.
    await session.execute(select(func.pg_advisory_xact_lock(7311001)))
    user_count = await session.scalar(select(func.count()).select_from(User))
    if user_count and not settings.allow_registration:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "registration is disabled")
    exists = await session.scalar(select(User.id).where(func.lower(User.email) == body.email.lower()))
    if exists:
        raise HTTPException(status.HTTP_409_CONFLICT, "email already registered")
    user = User(
        email=body.email.lower(),
        password_hash=hash_password(body.password),
        display_name=body.display_name,
        role=Role.ADMIN if not user_count else Role.OWNER,
    )
    session.add(user)
    await session.flush()
    await audit(session, owner_id=user.id, actor=f"user:{user.id}", action="auth.register", ip=client_ip(request))
    return user


@router.post("/login", response_model=TokenPair)
async def login(body: LoginRequest, request: Request, session: Session) -> TokenPair:
    ip = client_ip(request)
    await enforce_rate_limit("login", ip, get_settings().rate_limit_login_per_minute)
    await enforce_rate_limit("login-email", body.email.lower(), get_settings().rate_limit_login_per_minute)
    user = await session.scalar(select(User).where(func.lower(User.email) == body.email.lower()))
    ok = verify_password(user.password_hash if user else DUMMY_HASH, body.password)
    if not (user and ok and user.is_active):
        await audit(session, owner_id=None, actor="anonymous", action="auth.login_failed", ip=ip)
        await session.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")
    user.last_login_at = datetime.now(UTC)
    await audit(session, owner_id=user.id, actor=f"user:{user.id}", action="auth.login", ip=ip)
    return _pair(user)


@router.post("/refresh", response_model=TokenPair)
async def refresh(body: RefreshRequest, session: Session) -> TokenPair:
    bad = HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")
    try:
        payload = decode_token(body.refresh_token, "refresh")
        user = await session.get(User, uuid.UUID(payload["sub"]))
    except (TokenError, ValueError):
        raise bad from None
    if user is None or not user.is_active or user.token_version != payload["ver"]:
        raise bad
    return _pair(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(user: CurrentUser, session: Session) -> None:
    """Invalidates every outstanding token for the user."""
    user.token_version += 1
    await audit(session, owner_id=user.id, actor=f"user:{user.id}", action="auth.logout")


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> User:
    return user
