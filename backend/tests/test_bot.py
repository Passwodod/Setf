import asyncio
import itertools

import pytest
from sqlalchemy import text

from app.bot.factory import get_bot
from app.core.db import get_engine, get_sessionmaker
from app.models.base import ApprovalStatus
from app.services import approvals as approvals_svc
from app.services.consent_invite import make_token
from app.services.telegram_send import AiogramSender
from tests.fake_bot import make_bot

ADMIN_TG = 7524046634
PERSON_TG = 555001
SECRET = "test-webhook-secret"
_uid = itertools.count(1)


def _msg(text_, uid, chat_id=None):
    m = {"message_id": next(_uid), "date": 1700000000, "chat": {"id": chat_id or uid, "type": "private"},
         "from": {"id": uid, "is_bot": False, "first_name": "T"}, "text": text_}
    if text_.startswith("/"):
        m["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text_.split()[0])}]
    return m


class Tg:
    def __init__(self, client, sess):
        self.client, self.sess = client, sess

    async def post(self, payload, secret=SECRET):
        payload = {"update_id": next(_uid) + 10_000, **payload}
        headers = {"X-Telegram-Bot-Api-Secret-Token": secret} if secret else {}
        return await self.client.post("/api/v1/telegram/webhook", json=payload, headers=headers)

    async def say(self, uid, text_):
        r = await self.post({"message": _msg(text_, uid)})
        assert r.status_code == 200, r.text

    async def tap(self, uid, data):
        msg = _msg("card", 1)
        msg["chat"]["id"] = uid
        r = await self.post({"callback_query": {"id": str(next(_uid)), "from": {"id": uid, "is_bot": False, "first_name": "T"},
                                                "chat_instance": "ci", "data": data, "message": msg}})
        assert r.status_code == 200, r.text

    def mark(self):
        self._mark = len(self.sess.calls)

    def out(self, chat_id):
        return [d for m, d in self.sess.calls[getattr(self, "_mark", 0):] if m == "sendMessage" and d.get("chat_id") == chat_id]

    def last_text(self):
        return [d for m, d in self.sess.calls if m in ("sendMessage", "editMessageText")][-1]["text"]

    def answers(self):
        return [d for m, d in self.sess.calls if m == "answerCallbackQuery"]


@pytest.fixture
def tg(client, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "admin_telegram_ids", str(ADMIN_TG))
    from pydantic import SecretStr

    monkeypatch.setattr(get_settings(), "telegram_webhook_secret", SecretStr(SECRET))
    bot, sess = make_bot()
    client._transport.app.dependency_overrides[get_bot] = lambda: bot
    return Tg(client, sess)


async def link_admin(tg, alice):
    await tg.say(ADMIN_TG, "/start")


async def person_id(tg, name="Alex"):
    await tg.say(ADMIN_TG, f"/add_person {name} @alex_01")
    async with get_engine().connect() as c:
        return (await c.execute(text("SELECT id FROM persons WHERE display_name=:n"), {"n": name})).scalar()


async def consent(tg, pid):
    token = make_token(pid)
    await tg.say(PERSON_TG, f"/start c_{token}")
    await tg.tap(PERSON_TG, f"cs:yes:{token}")


async def eligible_person(tg, alice):
    await link_admin(tg, alice)
    pid = await person_id(tg)
    await consent(tg, pid)
    tg.mark()
    return pid


# ---- webhook security -----------------------------------------------------------------------
async def test_webhook_requires_secret(tg):
    assert (await tg.post({"message": _msg("/start", ADMIN_TG)}, secret=None)).status_code == 403
    assert (await tg.post({"message": _msg("/start", ADMIN_TG)}, secret="wrong")).status_code == 403
    assert tg.sess.calls == []


async def test_webhook_rejects_garbage(tg):
    r = await tg.client.post("/api/v1/telegram/webhook", content=b"not json", headers={"X-Telegram-Bot-Api-Secret-Token": SECRET})
    assert r.status_code == 400


async def test_duplicate_update_processed_once(tg, alice):
    payload = {"update_id": 424242, "message": _msg("/start", ADMIN_TG)}
    h = {"X-Telegram-Bot-Api-Secret-Token": SECRET}
    assert (await tg.client.post("/api/v1/telegram/webhook", json=payload, headers=h)).json().get("duplicate") is None
    assert (await tg.client.post("/api/v1/telegram/webhook", json=payload, headers=h)).json()["duplicate"] is True
    assert len(tg.sess.sent()) == 1


# ---- admin gate -----------------------------------------------------------------------------
async def test_non_admin_cannot_use_admin_commands(tg, alice):
    await tg.say(999, "/add_person Mallory")
    await tg.say(999, "/people")
    async with get_engine().connect() as c:
        assert (await c.execute(text("SELECT count(*) FROM persons"))).scalar() == 0
    assert all("Created" not in d["text"] for d in tg.sess.sent())


async def test_admin_links_to_platform_account(tg, alice):
    await link_admin(tg, alice)
    async with get_engine().connect() as c:
        assert (await c.execute(text("SELECT telegram_user_id FROM users"))).scalar() == ADMIN_TG
    assert "admin menu" in tg.last_text()


# ---- person management ----------------------------------------------------------------------
async def test_person_management_via_bot(tg, alice):
    await link_admin(tg, alice)
    pid = await person_id(tg)
    assert pid
    await tg.say(ADMIN_TG, "/note alex: likes chess")
    await tg.say(ADMIN_TG, "/profile alex")
    assert "likes chess" in tg.last_text()
    await tg.say(ADMIN_TG, "/people")
    assert any(m == "sendMessage" and "profile(s)" in d["text"] for m, d in tg.sess.calls)
    await tg.tap(ADMIN_TG, f"pp:del:{pid}")
    await tg.tap(ADMIN_TG, f"pp:delok:{pid}")
    async with get_engine().connect() as c:
        assert (await c.execute(text("SELECT count(*) FROM persons"))).scalar() == 0


async def test_callback_with_forged_person_id_is_inert(tg, alice, bob):
    await link_admin(tg, alice)
    async with get_engine().begin() as c:
        await c.execute(text("INSERT INTO persons(id,owner_id,display_name) SELECT gen_random_uuid(),id,'Bobs' FROM users WHERE email='bob@example.com'"))
        pid = (await c.execute(text("SELECT id FROM persons"))).scalar()
    await tg.tap(ADMIN_TG, f"pp:delok:{pid}")
    async with get_engine().connect() as c:
        assert (await c.execute(text("SELECT count(*) FROM persons"))).scalar() == 1


# ---- consent --------------------------------------------------------------------------------
async def test_consent_flow_grant_and_revoke(tg, alice):
    await link_admin(tg, alice)
    pid = await person_id(tg)
    token = make_token(pid)
    await tg.say(PERSON_TG, f"/start c_{token}")
    assert "automated assistant" in tg.last_text()
    async with get_engine().connect() as c:
        assert (await c.execute(text("SELECT count(*) FROM consent_records"))).scalar() == 0  # nothing before clicking
    await tg.tap(PERSON_TG, f"cs:yes:{token}")
    async with get_engine().connect() as c:
        assert (await c.execute(text("SELECT bot_chat_id FROM persons"))).scalar() == PERSON_TG
        assert (await c.execute(text("SELECT count(*) FROM consent_records WHERE status='granted'"))).scalar() == 2
    await tg.say(PERSON_TG, "/revoke")
    async with get_engine().connect() as c:
        assert (await c.execute(text("SELECT bot_chat_id FROM persons"))).scalar() is None
        latest = (await c.execute(text("SELECT status FROM consent_records ORDER BY recorded_at DESC, id LIMIT 1"))).scalar()
    assert latest == "revoked"


async def test_declined_and_tampered_tokens(tg, alice):
    await link_admin(tg, alice)
    pid = await person_id(tg)
    token = make_token(pid)
    await tg.tap(PERSON_TG, f"cs:no:{token}")
    async with get_engine().connect() as c:
        assert (await c.execute(text("SELECT bot_chat_id FROM persons"))).scalar() is None
    forged = token[:-2] + ("AA" if not token.endswith("AA") else "BB")
    await tg.tap(PERSON_TG, f"cs:yes:{forged}")
    async with get_engine().connect() as c:
        assert (await c.execute(text("SELECT bot_chat_id FROM persons"))).scalar() is None
        assert (await c.execute(text("SELECT count(*) FROM consent_records WHERE status='granted'"))).scalar() == 0


async def test_inbound_stored_only_with_consent(tg, alice):
    await eligible_person(tg, alice)
    await tg.say(PERSON_TG, "I started learning Rust")
    await tg.say(PERSON_TG, "/revoke")
    await tg.say(PERSON_TG, "this must not be stored")
    async with get_engine().connect() as c:
        rows = (await c.execute(text("SELECT direction, text FROM conversation_messages"))).all()
    assert rows == [("inbound", "I started learning Rust")]


# ---- approval center ------------------------------------------------------------------------
async def create_approval(tg, alice, pid, txt="Hi Alex, how is the Rust learning going?"):
    r = await tg.client.post("/api/v1/approvals", json={"person_id": str(pid), "text": txt}, headers=alice["headers"])
    return r


async def test_cannot_queue_message_without_consent(tg, alice):
    await link_admin(tg, alice)
    pid = await person_id(tg)
    r = await create_approval(tg, alice, pid)
    assert r.status_code == 409


async def test_full_approval_flow_sends_once(tg, alice):
    pid = await eligible_person(tg, alice)
    r = await create_approval(tg, alice, pid)
    assert r.status_code == 201
    aid = r.json()["id"]
    notice = tg.sess.sent(chat_id=ADMIN_TG)[-1]
    assert "Rust" in notice["text"] and len(notice["reply_markup"]["inline_keyboard"][0]) + len(notice["reply_markup"]["inline_keyboard"][1]) == 4
    assert tg.out(PERSON_TG) == []  # nothing goes out before approval

    await tg.tap(ADMIN_TG, f"ap:send:{aid}")
    await tg.tap(ADMIN_TG, f"ap:send:{aid}")  # double click
    delivered = tg.out(PERSON_TG)
    assert [d["text"] for d in delivered] == ["Hi Alex, how is the Rust learning going?"]
    got = (await tg.client.get(f"/api/v1/approvals/{aid}", headers=alice["headers"])).json()
    assert got["status"] == "sent"
    async with get_engine().connect() as c:
        assert (await c.execute(text("SELECT count(*) FROM conversation_messages WHERE direction='outbound'"))).scalar() == 1


async def test_non_admin_cannot_approve(tg, alice):
    pid = await eligible_person(tg, alice)
    aid = (await create_approval(tg, alice, pid)).json()["id"]
    await tg.tap(PERSON_TG, f"ap:send:{aid}")  # the recipient himself tries
    await tg.tap(31337, f"ap:send:{aid}")
    assert tg.out(PERSON_TG) == []
    assert (await tg.client.get(f"/api/v1/approvals/{aid}", headers=alice["headers"])).json()["status"] == "pending"


async def test_reject_and_cancel(tg, alice):
    pid = await eligible_person(tg, alice)
    a1 = (await create_approval(tg, alice, pid)).json()["id"]
    a2 = (await create_approval(tg, alice, pid, "second")).json()["id"]
    await tg.tap(ADMIN_TG, f"ap:rej:{a1}")
    assert (await tg.client.post(f"/api/v1/approvals/{a2}/cancel", headers=alice["headers"])).json()["status"] == "cancelled"
    for a in (a1, a2):
        await tg.tap(ADMIN_TG, f"ap:send:{a}")
    assert tg.out(PERSON_TG) == []


async def test_edit_flow_changes_text_before_send(tg, alice):
    pid = await eligible_person(tg, alice)
    aid = (await create_approval(tg, alice, pid)).json()["id"]
    await tg.tap(ADMIN_TG, f"ap:edit:{aid}")
    await tg.say(ADMIN_TG, "Edited text")
    got = (await tg.client.get(f"/api/v1/approvals/{aid}", headers=alice["headers"])).json()
    assert got["text"] == "Edited text" and got["revision"] == 2
    await tg.tap(ADMIN_TG, f"ap:send:{aid}")
    assert [d["text"] for d in tg.out(PERSON_TG)] == ["Edited text"]


async def test_regenerate_is_honest_about_missing_ai(tg, alice):
    pid = await eligible_person(tg, alice)
    aid = (await create_approval(tg, alice, pid)).json()["id"]
    await tg.tap(ADMIN_TG, f"ap:regen:{aid}")
    assert "AI engine" in tg.answers()[-1]["text"]


async def test_expired_approval_not_sent(tg, alice):
    pid = await eligible_person(tg, alice)
    aid = (await create_approval(tg, alice, pid)).json()["id"]
    async with get_engine().begin() as c:
        await c.execute(text("UPDATE approvals SET expires_at = now() - interval '1 minute'"))
    await tg.tap(ADMIN_TG, f"ap:send:{aid}")
    assert tg.out(PERSON_TG) == []
    assert (await tg.client.get(f"/api/v1/approvals/{aid}", headers=alice["headers"])).json()["status"] == "expired"


async def test_consent_revoked_after_queueing_blocks_send(tg, alice):
    pid = await eligible_person(tg, alice)
    aid = (await create_approval(tg, alice, pid)).json()["id"]
    await tg.say(PERSON_TG, "/revoke")
    tg.mark()
    await tg.tap(ADMIN_TG, f"ap:send:{aid}")
    assert tg.out(PERSON_TG) == []
    got = (await tg.client.get(f"/api/v1/approvals/{aid}", headers=alice["headers"])).json()
    assert got["status"] == "failed" and got["last_error"]


async def test_telegram_forbidden_recorded_as_failure(tg, alice):
    pid = await eligible_person(tg, alice)
    aid = (await create_approval(tg, alice, pid)).json()["id"]
    tg.sess.forbidden_chats.add(PERSON_TG)
    await tg.tap(ADMIN_TG, f"ap:send:{aid}")
    got = (await tg.client.get(f"/api/v1/approvals/{aid}", headers=alice["headers"])).json()
    assert got["status"] == "failed" and "blocked" in got["last_error"]
    await tg.tap(ADMIN_TG, f"ap:send:{aid}")  # failed is terminal: no automatic or repeated resend
    assert tg.out(PERSON_TG) == []


async def test_concurrent_sends_deliver_exactly_once(tg, alice):
    pid = await eligible_person(tg, alice)
    aid = (await create_approval(tg, alice, pid)).json()["id"]
    sender = AiogramSender(tg.client._transport.app.dependency_overrides[get_bot]())
    me = (await tg.client.get("/api/v1/auth/me", headers=alice["headers"])).json()
    import uuid

    async def attempt():
        async with get_sessionmaker()() as s:
            try:
                return await approvals_svc.approve_and_send(s, sender, uuid.UUID(me["id"]), uuid.UUID(aid), "test")
            except approvals_svc.InvalidState:
                return None

    results = await asyncio.gather(*[attempt() for _ in range(5)])
    assert sum(1 for r in results if r is not None and r.status == ApprovalStatus.SENT) == 1
    assert len(tg.out(PERSON_TG)) == 1


async def test_approvals_isolated_between_users(tg, alice, bob):
    pid = await eligible_person(tg, alice)
    aid = (await create_approval(tg, alice, pid)).json()["id"]
    assert (await tg.client.get(f"/api/v1/approvals/{aid}", headers=bob["headers"])).status_code == 404
    assert (await tg.client.post(f"/api/v1/approvals/{aid}/send", headers=bob["headers"])).status_code == 404
    assert (await tg.client.get("/api/v1/approvals", headers=bob["headers"])).json()["total"] == 0
    r = await tg.client.post("/api/v1/approvals", json={"person_id": str(pid), "text": "x"}, headers=bob["headers"])
    assert r.status_code == 404
