import inspect
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from app.core.db import get_engine
from app.main import create_app  # noqa: F401
from app.services import mtproto
from app.services.mtproto import CodeInvalid, Dialog, PasswordRequired, RemoteMessage, SignedIn, get_gateway

API_HASH = "0123456789abcdef0123456789abcdef"


class FakeGateway:
    def __init__(self):
        self.messages = {}
        self.revoked = False
        self.logged_out = False
        self.needs_password = False

    async def send_code(self, api_id, api_hash, phone):
        return "SESSION-PRE", "hash1"

    async def sign_in(self, api_id, api_hash, session, phone, code, phone_code_hash, password):
        if code != "12345":
            raise CodeInvalid
        if self.needs_password and not password:
            raise PasswordRequired
        return SignedIn("SESSION-SECRET-STRING", 777, "me")

    async def list_dialogs(self, api_id, api_hash, session):
        if self.revoked:
            raise mtproto.AuthRevoked
        return [Dialog(-1001, "Dev chat", "group"), Dialog(42, "Alex", "conversation")]

    async def fetch_messages(self, api_id, api_hash, session, peer_id, min_id, limit):
        if self.revoked:
            raise mtproto.AuthRevoked
        return [m for m in self.messages.get(peer_id, []) if m.id > min_id][:limit]

    async def log_out(self, api_id, api_hash, session):
        self.logged_out = True


@pytest.fixture
def gw(client):
    fake = FakeGateway()
    client._transport.app.dependency_overrides[get_gateway] = lambda: fake
    return fake


def msg(i, sender=42, text_="hello"):
    return RemoteMessage(i, sender, f"{text_} {i}", datetime(2026, 1, 1, tzinfo=UTC), None)


async def connect(client, headers, label="main"):
    r = await client.post("/api/v1/telegram/accounts/connect/start", headers=headers, json={
        "label": label, "api_id": 123, "api_hash": API_HASH, "phone": "+77011234567"})
    assert r.status_code == 200, r.text
    r = await client.post("/api/v1/telegram/accounts/connect/verify", headers=headers,
                          json={"pending_id": r.json()["pending_id"], "code": "12345"})
    assert r.status_code == 201, r.text
    return r.json()


async def test_connect_stores_only_ciphertext(client, alice, gw):
    acc = await connect(client, alice["headers"])
    assert acc["phone_masked"].endswith("4567") and "7701" not in acc["phone_masked"]
    assert "session" not in str(acc).lower() or "SESSION-SECRET" not in str(acc)
    async with get_engine().connect() as conn:
        row = (await conn.execute(text("SELECT session_enc, api_hash_enc FROM telegram_accounts"))).one()
    assert "SESSION-SECRET" not in row[0] and API_HASH not in row[1]


async def test_wrong_code_and_2fa(client, alice, gw):
    h = alice["headers"]
    p = (await client.post("/api/v1/telegram/accounts/connect/start", headers=h, json={
        "label": "x", "api_id": 1, "api_hash": API_HASH, "phone": "+77011234567"})).json()["pending_id"]
    r = await client.post("/api/v1/telegram/accounts/connect/verify", headers=h, json={"pending_id": p, "code": "00000"})
    assert r.status_code == 400
    gw.needs_password = True
    r = await client.post("/api/v1/telegram/accounts/connect/verify", headers=h, json={"pending_id": p, "code": "12345"})
    assert r.status_code == 401
    r = await client.post("/api/v1/telegram/accounts/connect/verify", headers=h,
                          json={"pending_id": p, "code": "12345", "password": "pw-2fa"})
    assert r.status_code == 201
    # pending login is single-use
    r = await client.post("/api/v1/telegram/accounts/connect/verify", headers=h, json={"pending_id": p, "code": "12345"})
    assert r.status_code == 404


async def test_pending_login_not_usable_by_other_user(client, alice, bob, gw):
    p = (await client.post("/api/v1/telegram/accounts/connect/start", headers=alice["headers"], json={
        "label": "x", "api_id": 1, "api_hash": API_HASH, "phone": "+77011234567"})).json()["pending_id"]
    r = await client.post("/api/v1/telegram/accounts/connect/verify", headers=bob["headers"], json={"pending_id": p, "code": "12345"})
    assert r.status_code == 404


async def test_multiple_accounts_isolated_between_users(client, alice, bob, gw):
    a = await connect(client, alice["headers"])
    assert (await client.get(f"/api/v1/telegram/accounts/{a['id']}", headers=bob["headers"])).status_code == 404
    assert (await client.delete(f"/api/v1/telegram/accounts/{a['id']}", headers=bob["headers"])).status_code == 404
    assert (await client.get(f"/api/v1/telegram/accounts/{a['id']}/dialogs", headers=bob["headers"])).status_code == 404
    assert (await client.get("/api/v1/telegram/accounts", headers=bob["headers"])).json() == []
    assert len((await client.get("/api/v1/telegram/accounts", headers=alice["headers"])).json()) == 1


async def test_group_source_requires_notice_and_imports_with_dedup(client, alice, gw):
    h = alice["headers"]
    acc = await connect(client, h)
    base = f"/api/v1/telegram/accounts/{acc['id']}"
    body = {"kind": "group", "peer_id": -1001, "title": "Dev chat"}
    assert (await client.post(f"{base}/sources", headers=h, json=body)).status_code == 409
    src = (await client.post(f"{base}/sources", headers=h, json={**body, "members_notified": True})).json()
    gw.messages[-1001] = [msg(1), msg(2), msg(3)]
    r = (await client.post(f"{base}/sources/{src['id']}/import", headers=h)).json()
    assert (r["status"], r["stored"], r["duplicates"]) == ("completed", 3, 0)
    gw.messages[-1001].append(msg(4))
    r = (await client.post(f"{base}/sources/{src['id']}/import", headers=h)).json()
    assert r["stored"] == 1  # incremental via last_message_id
    # forced redelivery of already stored ids is deduplicated by the DB constraint
    async with get_engine().begin() as conn:
        await conn.execute(text("UPDATE telegram_sources SET last_message_id = 0"))
    r = (await client.post(f"{base}/sources/{src['id']}/import", headers=h)).json()
    assert (r["stored"], r["duplicates"]) == (0, 4)
    assert len((await client.get(f"{base}/imports", headers=h)).json()) == 3


async def test_nothing_imported_by_default_and_disabled_source_blocked(client, alice, gw):
    h = alice["headers"]
    acc = await connect(client, h)
    base = f"/api/v1/telegram/accounts/{acc['id']}"
    assert (await client.get(f"{base}/sources", headers=h)).json() == []
    src = (await client.post(f"{base}/sources", headers=h, json={"kind": "channel", "peer_id": -5, "title": "C", "members_notified": True})).json()
    await client.patch(f"{base}/sources/{src['id']}", headers=h, json={"enabled": False})
    gw.messages[-5] = [msg(1)]
    r = (await client.post(f"{base}/sources/{src['id']}/import", headers=h)).json()
    assert r["status"] == "failed" and r["stored"] == 0


async def test_private_conversation_needs_person_consent_and_revocation_stops_import(client, alice, gw):
    h = alice["headers"]
    acc = await connect(client, h)
    base = f"/api/v1/telegram/accounts/{acc['id']}"
    pid = (await client.post("/api/v1/persons", headers=h, json={"display_name": "Alex"})).json()["id"]
    body = {"kind": "conversation", "peer_id": 42, "title": "Alex", "person_id": pid}
    assert (await client.post(f"{base}/sources", headers=h, json=body)).status_code == 409  # adding a person != consent
    assert (await client.post(f"{base}/sources", headers=h, json={**body, "person_id": None})).status_code == 409
    consent = {"person_id": pid, "scope": "profiling", "method": "bot_button"}
    await client.post("/api/v1/privacy/consents", headers=h, json={**consent, "status": "granted"})
    src = (await client.post(f"{base}/sources", headers=h, json=body)).json()
    gw.messages[42] = [msg(1), msg(2)]
    assert (await client.post(f"{base}/sources/{src['id']}/import", headers=h)).json()["stored"] == 2
    await client.post("/api/v1/privacy/consents", headers=h, json={**consent, "status": "revoked"})
    gw.messages[42].append(msg(3))
    r = (await client.post(f"{base}/sources/{src['id']}/import", headers=h)).json()
    assert r["status"] == "failed" and r["stored"] == 0


async def test_foreign_person_cannot_be_linked(client, alice, bob, gw):
    acc = await connect(client, bob["headers"])
    pid = (await client.post("/api/v1/persons", headers=alice["headers"], json={"display_name": "A"})).json()["id"]
    r = await client.post(f"/api/v1/telegram/accounts/{acc['id']}/sources", headers=bob["headers"],
                          json={"kind": "conversation", "peer_id": 1, "title": "t", "person_id": pid})
    assert r.status_code == 404


async def test_excluded_author_messages_skipped(client, alice, gw):
    h = alice["headers"]
    acc = await connect(client, h)
    base = f"/api/v1/telegram/accounts/{acc['id']}"
    pid = (await client.post("/api/v1/persons", headers=h, json={"display_name": "Opt-out", "do_not_profile": True})).json()["id"]
    async with get_engine().begin() as conn:
        await conn.execute(text("INSERT INTO person_identifiers(id,owner_id,person_id,kind,value) SELECT gen_random_uuid(),owner_id,id,'telegram_id','99' FROM persons"))
    src = (await client.post(f"{base}/sources", headers=h, json={"kind": "group", "peer_id": -9, "title": "G", "members_notified": True})).json()
    gw.messages[-9] = [msg(1, sender=99), msg(2, sender=5)]
    r = (await client.post(f"{base}/sources/{src['id']}/import", headers=h)).json()
    assert (r["stored"], r["skipped"]) == (1, 1)


async def test_revoked_session_marks_needs_reauth(client, alice, gw):
    h = alice["headers"]
    acc = await connect(client, h)
    gw.revoked = True
    assert (await client.get(f"/api/v1/telegram/accounts/{acc['id']}/dialogs", headers=h)).status_code == 409
    assert (await client.get(f"/api/v1/telegram/accounts/{acc['id']}", headers=h)).json()["status"] == "needs_reauth"


async def test_disconnect_wipes_session_and_optionally_data(client, alice, gw):
    h = alice["headers"]
    acc = await connect(client, h)
    base = f"/api/v1/telegram/accounts/{acc['id']}"
    src = (await client.post(f"{base}/sources", headers=h, json={"kind": "group", "peer_id": -7, "title": "G", "members_notified": True})).json()
    gw.messages[-7] = [msg(1)]
    await client.post(f"{base}/sources/{src['id']}/import", headers=h)
    assert (await client.delete(f"{base}?delete_data=true", headers=h)).status_code == 204
    assert gw.logged_out
    sec = (await client.get(f"{base}/security", headers=h)).json()
    assert sec["has_session"] is False and sec["status"] == "disconnected"
    async with get_engine().connect() as conn:
        assert (await conn.execute(text("SELECT session_enc FROM telegram_accounts"))).scalar() is None
        assert (await conn.execute(text("SELECT count(*) FROM messages"))).scalar() == 0


async def test_security_endpoint_leaks_no_secrets(client, alice, gw):
    acc = await connect(client, alice["headers"])
    r = await client.get(f"/api/v1/telegram/accounts/{acc['id']}/security", headers=alice["headers"])
    assert "SESSION-SECRET" not in r.text and API_HASH not in r.text
    assert r.json()["capabilities"] == ["read_selected_sources"]


def test_gateway_is_read_only():
    names = {n for n, _ in inspect.getmembers(mtproto.TelethonGateway, inspect.isfunction)}
    assert not {n for n in names if any(w in n for w in ("send_message", "edit", "delete", "forward"))}
    assert "send_code" in names  # login code request is the only 'send'


def test_encryption_roundtrip_and_rotation(monkeypatch):
    from cryptography.fernet import Fernet

    from app.core.config import get_settings
    from app.security import crypto

    old = get_settings().session_encryption_keys.get_secret_value()
    token = crypto.encrypt_secret("abc")
    new_key = Fernet.generate_key().decode()
    monkeypatch.setattr(get_settings(), "session_encryption_keys", type(get_settings().session_encryption_keys)(f"{new_key},{old}"))
    assert crypto.decrypt_secret(token) == "abc"
    rotated = crypto.rotate_secret(token)
    monkeypatch.setattr(get_settings(), "session_encryption_keys", type(get_settings().session_encryption_keys)(new_key))
    assert crypto.decrypt_secret(rotated) == "abc"
