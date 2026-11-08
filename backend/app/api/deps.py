import uuid
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.models import User
from app.models.base import Role
from app.security.tokens import TokenError, decode_token

bearer = HTTPBearer(auto_error=False)
Session = Annotated[AsyncSession, Depends(get_session)]


async def current_user(
    session: Session, creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]
) -> User:
    unauthorized = HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials", headers={"WWW-Authenticate": "Bearer"})
    if creds is None:
        raise unauthorized
    try:
        payload = decode_token(creds.credentials, "access")
        user_id = uuid.UUID(payload["sub"])
    except (TokenError, ValueError):
        raise unauthorized from None
    user = await session.get(User, user_id)
    if user is None or not user.is_active or user.token_version != payload["ver"]:
        raise unauthorized
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def require_role(*roles: Role):
    async def checker(user: CurrentUser) -> User:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "insufficient permissions")
        return user

    return checker
