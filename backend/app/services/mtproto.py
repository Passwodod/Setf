"""Read-only MTProto access through Telethon. The gateway deliberately exposes no send/edit/delete
operations: outbound messages go only through the Bot API behind admin approval."""
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


class GatewayError(Exception):
    pass


class CodeInvalid(GatewayError):
    pass


class PasswordRequired(GatewayError):
    pass


class AuthRevoked(GatewayError):
    pass


@dataclass
class SignedIn:
    session: str
    user_id: int
    username: str | None


@dataclass
class Dialog:
    peer_id: int
    title: str
    kind: str  # group | channel | conversation


@dataclass
class RemoteMessage:
    id: int
    sender_id: int | None
    text: str
    date: datetime
    reply_to_id: int | None


class MTProtoGateway(Protocol):
    async def send_code(self, api_id: int, api_hash: str, phone: str) -> tuple[str, str]: ...
    async def sign_in(self, api_id: int, api_hash: str, session: str, phone: str, code: str,
                      phone_code_hash: str, password: str | None) -> SignedIn: ...
    async def list_dialogs(self, api_id: int, api_hash: str, session: str) -> list[Dialog]: ...
    async def fetch_messages(self, api_id: int, api_hash: str, session: str, peer_id: int,
                             min_id: int, limit: int) -> list[RemoteMessage]: ...
    async def log_out(self, api_id: int, api_hash: str, session: str) -> None: ...


class TelethonGateway:
    @staticmethod
    def _client(api_id: int, api_hash: str, session: str):
        from telethon import TelegramClient
        from telethon.sessions import StringSession

        return TelegramClient(StringSession(session), api_id, api_hash)

    async def send_code(self, api_id, api_hash, phone):
        from telethon.errors import RPCError

        client = self._client(api_id, api_hash, "")
        await client.connect()
        try:
            sent = await client.send_code_request(phone)
            return client.session.save(), sent.phone_code_hash
        except RPCError as exc:
            raise GatewayError(type(exc).__name__) from exc
        finally:
            await client.disconnect()

    async def sign_in(self, api_id, api_hash, session, phone, code, phone_code_hash, password):
        from telethon.errors import (
            PhoneCodeEmptyError, PhoneCodeExpiredError, PhoneCodeInvalidError, RPCError,
            SessionPasswordNeededError,
        )

        client = self._client(api_id, api_hash, session)
        await client.connect()
        try:
            try:
                await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
            except SessionPasswordNeededError:
                if not password:
                    raise PasswordRequired from None
                await client.sign_in(password=password)
            except (PhoneCodeInvalidError, PhoneCodeExpiredError, PhoneCodeEmptyError) as exc:
                raise CodeInvalid from exc
            me = await client.get_me()
            return SignedIn(client.session.save(), me.id, me.username)
        except RPCError as exc:
            raise GatewayError(type(exc).__name__) from exc
        finally:
            await client.disconnect()

    async def list_dialogs(self, api_id, api_hash, session):
        from telethon.errors import AuthKeyError, RPCError, UnauthorizedError
        from telethon.tl.types import Channel, Chat, User

        client = self._client(api_id, api_hash, session)
        await client.connect()
        try:
            if not await client.is_user_authorized():
                raise AuthRevoked
            out = []
            async for d in client.iter_dialogs(limit=500):
                e = d.entity
                if isinstance(e, User):
                    if e.bot or e.is_self:
                        continue
                    out.append(Dialog(e.id, d.name or str(e.id), "conversation"))
                elif isinstance(e, Channel):
                    out.append(Dialog(d.id, d.name, "group" if e.megagroup else "channel"))
                elif isinstance(e, Chat):
                    out.append(Dialog(d.id, d.name, "group"))
            return out
        except (AuthKeyError, UnauthorizedError) as exc:
            raise AuthRevoked from exc
        except RPCError as exc:
            raise GatewayError(type(exc).__name__) from exc
        finally:
            await client.disconnect()

    async def fetch_messages(self, api_id, api_hash, session, peer_id, min_id, limit):
        from telethon.errors import AuthKeyError, RPCError, UnauthorizedError

        client = self._client(api_id, api_hash, session)
        await client.connect()
        try:
            if not await client.is_user_authorized():
                raise AuthRevoked
            msgs = []
            async for m in client.iter_messages(peer_id, limit=limit, min_id=min_id):
                if m.message:
                    msgs.append(RemoteMessage(m.id, m.sender_id, m.message, m.date, m.reply_to_msg_id))
            return sorted(msgs, key=lambda x: x.id)
        except (AuthKeyError, UnauthorizedError) as exc:
            raise AuthRevoked from exc
        except RPCError as exc:
            raise GatewayError(type(exc).__name__) from exc
        finally:
            await client.disconnect()

    async def log_out(self, api_id, api_hash, session):
        client = self._client(api_id, api_hash, session)
        await client.connect()
        try:
            if await client.is_user_authorized():
                await client.log_out()
        finally:
            await client.disconnect()


_gateway: MTProtoGateway = TelethonGateway()


def get_gateway() -> MTProtoGateway:
    return _gateway
