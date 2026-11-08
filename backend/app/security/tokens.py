import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

import jwt

from app.core.config import get_settings

TokenType = Literal["access", "refresh"]


class TokenError(Exception):
    pass


def create_token(user_id: uuid.UUID, token_type: TokenType, *, token_version: int) -> str:
    s = get_settings()
    now = datetime.now(UTC)
    ttl = timedelta(minutes=s.access_token_minutes) if token_type == "access" else timedelta(days=s.refresh_token_days)
    payload = {
        "sub": str(user_id),
        "typ": token_type,
        "ver": token_version,
        "jti": uuid.uuid4().hex,
        "iat": now,
        "exp": now + ttl,
    }
    return jwt.encode(payload, s.jwt_secret.get_secret_value(), algorithm=s.jwt_algorithm)


def decode_token(token: str, expected: TokenType) -> dict:
    s = get_settings()
    try:
        payload = jwt.decode(
            token,
            s.jwt_secret.get_secret_value(),
            algorithms=[s.jwt_algorithm],
            options={"require": ["exp", "sub", "typ", "ver"]},
        )
    except jwt.PyJWTError as exc:
        raise TokenError("invalid token") from exc
    if payload.get("typ") != expected:
        raise TokenError("wrong token type")
    return payload
