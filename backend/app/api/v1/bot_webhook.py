import hmac
import json
import logging
from typing import Annotated

from aiogram import Bot, Dispatcher
from aiogram.types import Update
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from app.bot.factory import get_bot, get_dispatcher
from app.core.config import get_settings
from app.core.redis import get_redis

router = APIRouter(prefix="/telegram", tags=["telegram-bot"])
log = logging.getLogger("sia.webhook")


@router.post("/webhook", include_in_schema=False)
async def webhook(
    request: Request,
    secret: Annotated[str | None, Header(alias="X-Telegram-Bot-Api-Secret-Token")] = None,
    bot: Bot | None = Depends(get_bot),
    dp: Dispatcher = Depends(get_dispatcher),
) -> dict:
    expected = get_settings().telegram_webhook_secret.get_secret_value()
    if not expected or secret is None or not hmac.compare_digest(secret.encode(), expected.encode()):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
    if bot is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "bot is not configured")
    try:
        update = Update.model_validate(json.loads(await request.body()), context={"bot": bot})
    except Exception:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid update") from None
    # Telegram redelivers on timeouts/5xx; process each update_id once.
    try:
        first = await get_redis().set(f"tgupd:{update.update_id}", "1", nx=True, ex=86400)
    except Exception:
        first = True  # Redis down: prefer possible reprocessing over dropping the update
    if not first:
        return {"ok": True, "duplicate": True}
    try:
        await dp.feed_update(bot, update)
    except Exception:
        log.exception("update %s failed", update.update_id)  # 200 anyway so Telegram does not hammer us
    return {"ok": True}
