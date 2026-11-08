"""aiogram wiring: bot/dispatcher singletons, per-update DB session, admin resolution."""
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import BaseFilter
from sqlalchemy import select

from app.core.config import admin_ids, get_settings
from app.core.db import get_sessionmaker
from app.models import User
from app.models.base import Role

log = logging.getLogger("sia.bot")
_bot: Bot | None = None
_dp: Dispatcher | None = None


class DbAndAdminMiddleware(BaseMiddleware):
    async def __call__(self, handler: Callable[[Any, dict], Awaitable[Any]], event: Any, data: dict) -> Any:
        from_user = data.get("event_from_user")
        async with get_sessionmaker()() as session:
            data["session"] = session
            data["user"] = None
            if from_user is not None and from_user.id in admin_ids():
                data["user"] = await resolve_admin(session, from_user.id)
            try:
                result = await handler(event, data)
                await session.commit()
                return result
            except Exception:
                await session.rollback()
                raise


async def resolve_admin(session, tg_id: int) -> User | None:
    """Only configured Telegram IDs can be admins. The first one links to the platform admin account."""
    user = await session.scalar(select(User).where(User.telegram_user_id == tg_id, User.is_active.is_(True)))
    if user is None:
        user = await session.scalar(select(User).where(
            User.role == Role.ADMIN, User.telegram_user_id.is_(None), User.is_active.is_(True)).order_by(User.created_at))
        if user is not None:
            user.telegram_user_id = tg_id
            await session.flush()
    return user


class IsAdmin(BaseFilter):
    async def __call__(self, event: Any, user: User | None = None) -> bool:
        return user is not None


def build_dispatcher() -> Dispatcher:
    from app.bot.handlers import admin, approvals, consent, fallback

    dp = Dispatcher()
    dp.update.outer_middleware(DbAndAdminMiddleware())
    dp.include_routers(consent.router, approvals.router, admin.router, fallback.router)
    return dp


def get_bot() -> Bot | None:
    global _bot
    token = get_settings().telegram_bot_token.get_secret_value()
    if _bot is None and token:
        _bot = Bot(token, default=DefaultBotProperties(parse_mode=None))
    return _bot


def get_dispatcher() -> Dispatcher:
    global _dp
    if _dp is None:
        _dp = build_dispatcher()
    return _dp
