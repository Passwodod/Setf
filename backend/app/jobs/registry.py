"""Job handler registry. A handler is `async def (session, job) -> dict | None`.
Raise PermanentJobError for failures that retrying cannot fix."""
import uuid
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BackgroundJob


class PermanentJobError(Exception):
    pass


Handler = Callable[[AsyncSession, BackgroundJob], Awaitable[dict | None]]
REGISTRY: dict[str, Handler] = {}
HANDLER_TIMEOUT = 240  # must stay below queue.VISIBILITY_SECONDS


def handler(kind: str):
    def deco(fn: Handler) -> Handler:
        REGISTRY[kind] = fn
        return fn

    return deco


def uid(payload: dict, key: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(payload[key]))
    except (KeyError, ValueError):
        raise PermanentJobError(f"payload field '{key}' missing or invalid") from None
