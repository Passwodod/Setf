from datetime import UTC, datetime

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import GetMe, SendMessage
from aiogram.types import Chat, Message, User


class RecordingSession(BaseSession):
    """Stands in for Telegram: records every Bot API call and returns well-formed results."""

    def __init__(self):
        super().__init__()
        self.calls: list[tuple[str, dict]] = []
        self.forbidden_chats: set[int] = set()
        self.failed: list[dict] = []
        self.bot_is_admin = True
        self._id = 1000

    async def make_request(self, bot, method, timeout=None):
        data = method.model_dump(exclude_none=True)
        if isinstance(method, SendMessage) and data.get("chat_id") in self.forbidden_chats:
            self.failed.append(data)
            raise TelegramForbiddenError(method=method, message="Forbidden: bot was blocked by the user")
        self.calls.append((method.__api_method__, data))
        if method.__api_method__ == "getChatMember":
            from aiogram.types import ChatMemberAdministrator, ChatMemberMember

            u = User(id=1, is_bot=True, first_name="SIA", username="sia_test_bot")
            if self.bot_is_admin:
                return ChatMemberAdministrator.model_construct(status="administrator", user=u)
            return ChatMemberMember(status="member", user=u)
        if isinstance(method, GetMe):
            return User(id=1, is_bot=True, first_name="SIA", username="sia_test_bot")
        chat_id = data.get("chat_id")
        if isinstance(method, SendMessage) and chat_id in self.forbidden_chats:
            raise TelegramForbiddenError(method=method, message="Forbidden: bot was blocked by the user")
        if method.__api_method__ in ("sendMessage", "editMessageText"):
            self._id += 1
            return Message(message_id=self._id, date=datetime.now(UTC), chat=Chat(id=chat_id or 0, type="private"),
                           text=data.get("text"))
        return True

    async def close(self):
        pass

    async def stream_content(self, *a, **k):
        if False:
            yield b""

    def sent(self, method="sendMessage", chat_id=None):
        return [d for m, d in self.calls if m == method and (chat_id is None or d.get("chat_id") == chat_id)]


def make_bot() -> tuple[Bot, RecordingSession]:
    sess = RecordingSession()
    return Bot("123456:TEST-TOKEN-NOT-REAL", session=sess), sess
