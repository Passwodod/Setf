"""Outbound Telegram Bot API access. Only the Bot API is used (never a user session)."""
from typing import Protocol


class SendError(Exception):
    """Delivery failed; `permanent` means retrying the same message will not help."""

    def __init__(self, message: str, permanent: bool = True):
        super().__init__(message)
        self.permanent = permanent


class TextSender(Protocol):
    async def send_text(self, chat_id: int, text: str) -> int: ...


class AiogramSender:
    def __init__(self, bot):
        self.bot = bot

    async def send_text(self, chat_id: int, text: str) -> int:
        from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError, TelegramRetryAfter

        try:
            msg = await self.bot.send_message(chat_id, text, parse_mode=None)
            return msg.message_id
        except TelegramForbiddenError as exc:
            raise SendError("recipient blocked the bot or never started it", True) from exc
        except TelegramRetryAfter as exc:
            raise SendError(f"rate limited by Telegram, retry after {exc.retry_after}s", False) from exc
        except TelegramAPIError as exc:
            raise SendError(f"telegram error: {exc.message}", True) from exc
