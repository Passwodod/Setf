from fastapi import APIRouter, HTTPException, status
from sqlalchemy import text

from app.api.deps import Session
from app.core.redis import get_redis

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(session: Session) -> dict:
    try:
        await session.execute(text("SELECT 1"))
        await get_redis().ping()
    except Exception:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "dependency unavailable") from None
    return {"status": "ready"}
