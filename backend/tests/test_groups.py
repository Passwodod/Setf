import itertools

import pytest
from sqlalchemy import text

from app.core.db import get_engine
from tests.test_bot import ADMIN_TG, Tg, link_admin, tg  # noqa: F401  (tg fixture)

CHAT = -1001234
MEMBER, OTHER = 8001, 8002
_ids = itertools.count(1)


def gmsg(text_, uid, chat=CHAT):
    m = {"message_id": next(_ids), "date": 1700000000, "chat": {"id": chat, "type": "supergroup", "title": "Dev chat"},
         "from": {"id": uid, "is_bot": False, "first_name": "M"}, "text": text_}
    if text_.startswith("/"):
        m["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text_.split()[0])}]
    return {"message": m}


async def say(tg, uid, text_, chat=CHAT):
    r = await tg.post(gmsg(text_, uid, chat))
    assert r.status_code == 200, r.text


async def count(where="true"):
    async with get_engine().connect() as c:
        return (await c.execute(text(f"SELECT count(*) FROM messages WHERE {where}"))).scalar()


async def connected(tg, alice):
    await link_admin(tg, alice)
    await say(tg, ADMIN_TG, "/connect_group")


async def test_nothing_collected_before_connect_or_enable(tg, alice):
    await link_admin(tg, alice)
    await say(tg, MEMBER, "hello unknown group")
    await connected(tg, alice)
    await say(tg, MEMBER, "hello connected but not enabled")
    assert await count() == 0


async def test_connect_posts_notice_and_checks_privacy_mode(tg, alice):
    await connected(tg, alice)
    texts = [d["text"] for d in tg.sess.sent(chat_id=CHAT)]
    assert any("Notice:" in t and "/optout" in t for t in texts)
    async with get_engine().connect() as c:
        row = (await c.execute(text("SELECT status, collection_enabled, notice_sent_at IS NOT NULL FROM groups"))).one()
    assert row == ("pending", False, True)


async def test_non_admin_cannot_connect_or_enable(tg, alice):
    await link_admin(tg, alice)
    await say(tg, MEMBER, "/connect_group")
    async with get_engine().connect() as c:
        assert (await c.execute(text("SELECT count(*) FROM groups"))).scalar() == 0
    await connected(tg, alice)
    await say(tg, MEMBER, "/enable_collection")
    await say(tg, MEMBER, "message")
    assert await count() == 0


async def test_enable_collect_dedup_and_consent_record(tg, alice):
    await connected(tg, alice)
    await say(tg, ADMIN_TG, "/enable_collection")
    await say(tg, MEMBER, "I just started learning Rust")
    await say(tg, OTHER, "я занимаюсь программированием")
    await say(tg, MEMBER, "/not_a_collected_command")
    assert await count() == 2
    async with get_engine().connect() as c:
        assert (await c.execute(text("SELECT count(*) FROM consent_records WHERE scope='group_collection' AND status='granted'"))).scalar() == 1
        assert (await c.execute(text("SELECT count(*) FROM message_sources WHERE source='telegram_webhook'"))).scalar() == 2
        assert (await c.execute(text("SELECT count(DISTINCT author_telegram_id) FROM messages"))).scalar() == 2
    # Telegram redelivery of the same message (new update_id, same message_id) is not stored twice
    payload = gmsg("dup", MEMBER)
    await tg.post(payload)
    await tg.post(payload)
    assert await count("text='dup'") == 1


async def test_bot_messages_and_forwards_ignored(tg, alice):
    await connected(tg, alice)
    await say(tg, ADMIN_TG, "/enable_collection")
    p = gmsg("from a bot", MEMBER)
    p["message"]["from"]["is_bot"] = True
    await tg.post(p)
    f = gmsg("forwarded text", MEMBER)
    f["message"]["forward_origin"] = {"type": "hidden_user", "date": 1700000000, "sender_user_name": "x"}
    await tg.post(f)
    assert await count() == 0


async def test_member_optout_deletes_history_and_blocks_future(tg, alice):
    await connected(tg, alice)
    await say(tg, ADMIN_TG, "/enable_collection")
    await say(tg, MEMBER, "first")
    await say(tg, OTHER, "other person's message")
    await say(tg, MEMBER, "/optout")
    assert await count(f"author_telegram_id={MEMBER}") == 0
    await say(tg, MEMBER, "after optout")
    assert await count(f"author_telegram_id={MEMBER}") == 0 and await count() == 1
    await say(tg, MEMBER, "/optin")
    await say(tg, MEMBER, "back again")
    assert await count(f"author_telegram_id={MEMBER}") == 1


async def test_stop_collection_and_purge(tg, alice):
    await connected(tg, alice)
    await say(tg, ADMIN_TG, "/enable_collection")
    await say(tg, MEMBER, "one")
    await say(tg, ADMIN_TG, "/stop_collection")
    await say(tg, MEMBER, "two")
    assert await count() == 1
    await say(tg, ADMIN_TG, "/purge_group")
    assert await count() == 0
    async with get_engine().connect() as c:
        assert (await c.execute(text("SELECT status FROM consent_records WHERE scope='group_collection' ORDER BY recorded_at DESC, id LIMIT 1"))).scalar() == "revoked"


async def test_known_person_do_not_profile_is_skipped_and_author_linked(tg, alice):
    await connected(tg, alice)
    await say(tg, ADMIN_TG, "/enable_collection")
    async with get_engine().begin() as c:
        await c.execute(text("INSERT INTO persons(id,owner_id,display_name,do_not_profile) SELECT gen_random_uuid(),id,'Linked',false FROM users"))
        await c.execute(text("INSERT INTO persons(id,owner_id,display_name,do_not_profile) SELECT gen_random_uuid(),id,'Private',true FROM users"))
        await c.execute(text(f"INSERT INTO person_identifiers(id,owner_id,person_id,kind,value) SELECT gen_random_uuid(),owner_id,id,'telegram_id',CASE display_name WHEN 'Linked' THEN '{MEMBER}' ELSE '{OTHER}' END FROM persons"))
    await say(tg, MEMBER, "linked author")
    await say(tg, OTHER, "should be skipped")
    assert await count() == 1
    async with get_engine().connect() as c:
        assert (await c.execute(text("SELECT p.display_name FROM messages m JOIN persons p ON p.id = m.author_person_id"))).scalar() == "Linked"


async def test_admin_commands_do_not_work_in_groups(tg, alice):
    await link_admin(tg, alice)
    await say(tg, ADMIN_TG, "/people")
    await say(tg, ADMIN_TG, "/approvals")
    assert tg.sess.sent(chat_id=CHAT) == []  # profile data never posted to a group


async def test_groups_api_view_disable_purge_delete_isolation(tg, alice, bob):
    h, hb = alice["headers"], bob["headers"]
    await connected(tg, alice)
    await say(tg, ADMIN_TG, "/enable_collection")
    await say(tg, MEMBER, "stored one")
    gid = (await tg.client.get("/api/v1/groups", headers=h)).json()["items"][0]["id"]
    assert (await tg.client.get(f"/api/v1/groups/{gid}/messages", headers=h)).json()["total"] == 1
    for method, path in (("get", ""), ("get", "/messages"), ("post", "/purge"), ("delete", "")):
        assert (await getattr(tg.client, method)(f"/api/v1/groups/{gid}{path}", headers=hb)).status_code == 404
    r = await tg.client.patch(f"/api/v1/groups/{gid}", json={"collection_enabled": False, "retention_days": 30}, headers=h)
    assert r.json()["collection_enabled"] is False and r.json()["retention_days"] == 30
    await say(tg, MEMBER, "not stored")
    assert await count() == 1
    assert (await tg.client.post(f"/api/v1/groups/{gid}/purge", headers=h)).json() == {"deleted_messages": 1}
    assert (await tg.client.delete(f"/api/v1/groups/{gid}", headers=h)).status_code == 204
    assert (await tg.client.get(f"/api/v1/groups/{gid}", headers=h)).status_code == 404


async def test_cannot_enable_via_api_without_notice(tg, alice):
    async with get_engine().begin() as c:
        await c.execute(text("INSERT INTO groups(id,owner_id,telegram_chat_id,title) SELECT gen_random_uuid(),id,-5,'No notice' FROM users"))
    gid = (await tg.client.get("/api/v1/groups", headers=alice["headers"])).json()["items"][0]["id"]
    r = await tg.client.patch(f"/api/v1/groups/{gid}", json={"collection_enabled": True}, headers=alice["headers"])
    assert r.status_code == 409


async def test_retention_sets_deadline_on_stored_messages(tg, alice):
    await connected(tg, alice)
    await say(tg, ADMIN_TG, "/enable_collection")
    gid = (await tg.client.get("/api/v1/groups", headers=alice["headers"])).json()["items"][0]["id"]
    await tg.client.patch(f"/api/v1/groups/{gid}", json={"retention_days": 7}, headers=alice["headers"])
    await say(tg, MEMBER, "expires in a week")
    async with get_engine().connect() as c:
        d = (await c.execute(text("SELECT retained_until - now() FROM message_sources"))).scalar()
    assert 6 <= d.days <= 7


async def test_privacy_mode_warning_and_flag(tg, alice):
    tg.sess.bot_is_admin = False
    await connected(tg, alice)
    assert any("privacy mode" in d["text"] for d in tg.sess.sent(chat_id=CHAT))
    async with get_engine().connect() as c:
        assert (await c.execute(text("SELECT bot_can_read_all FROM groups"))).scalar() is False
