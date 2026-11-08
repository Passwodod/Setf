import uuid

import jwt

from app.core.config import get_settings
from tests.conftest import make_user


async def test_unauthenticated_requests_rejected(client):
    for method, path in [("get", "/api/v1/persons"), ("post", "/api/v1/persons"), ("get", "/api/v1/auth/me")]:
        r = await getattr(client, method)(path)
        assert r.status_code == 401


async def test_invalid_tokens_rejected(client, alice):
    secret = get_settings().jwt_secret.get_secret_value()
    me = (await client.get("/api/v1/auth/me", headers=alice["headers"])).json()
    forged = jwt.encode({"sub": me["id"], "typ": "access", "ver": 0, "exp": 9999999999}, "wrong-secret-wrong-secret-wrong-secret", algorithm="HS256")
    expired = jwt.encode({"sub": me["id"], "typ": "access", "ver": 0, "exp": 1}, secret, algorithm="HS256")
    none_alg = jwt.encode({"sub": me["id"], "typ": "access", "ver": 0, "exp": 9999999999}, None, algorithm="none")
    for tok in ["garbage", forged, expired, none_alg, alice["tokens"]["refresh_token"]]:
        r = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 401, tok


async def test_cross_user_access_blocked(client, alice, bob):
    r = await client.post("/api/v1/persons", json={"display_name": "Secret friend"}, headers=alice["headers"])
    pid = r.json()["id"]
    assert (await client.get(f"/api/v1/persons/{pid}", headers=bob["headers"])).status_code == 404
    assert (await client.patch(f"/api/v1/persons/{pid}", json={"notes": "x"}, headers=bob["headers"])).status_code == 404
    assert (await client.delete(f"/api/v1/persons/{pid}", headers=bob["headers"])).status_code == 404
    assert (await client.get("/api/v1/persons", headers=bob["headers"])).json()["total"] == 0
    # untouched for the real owner
    assert (await client.get(f"/api/v1/persons/{pid}", headers=alice["headers"])).status_code == 200
    assert (await client.get(f"/api/v1/persons/{uuid.uuid4()}", headers=alice["headers"])).status_code == 404


async def test_mass_assignment_of_owner_rejected(client, alice, bob):
    me = (await client.get("/api/v1/auth/me", headers=bob["headers"])).json()
    r = await client.post("/api/v1/persons", json={"display_name": "A", "owner_id": me["id"]}, headers=alice["headers"])
    assert r.status_code == 422


async def test_sql_injection_in_search_is_inert(client, alice):
    h = alice["headers"]
    await client.post("/api/v1/persons", json={"display_name": "Normal"}, headers=h)
    for payload in ["'; DROP TABLE persons; --", "' OR '1'='1", "%", "_"]:
        r = await client.get("/api/v1/persons", params={"q": payload}, headers=h)
        assert r.status_code == 200
        assert r.json()["total"] == 0  # wildcards are escaped, not interpreted
    assert (await client.get("/api/v1/persons", headers=h)).json()["total"] == 1
    r = await client.get("/api/v1/persons", params={"sort": "display_name; DROP TABLE persons"}, headers=h)
    assert r.status_code == 422


async def test_xss_payload_stored_verbatim_and_served_as_json(client, alice):
    payload = "<script>alert(1)</script>"
    r = await client.post("/api/v1/persons", json={"display_name": payload}, headers=alice["headers"])
    assert r.status_code == 201
    r = await client.get("/api/v1/persons", headers=alice["headers"])
    assert r.headers["content-type"].startswith("application/json")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.json()["items"][0]["display_name"] == payload  # escaping is the renderer's job


async def test_deactivated_user_token_rejected(client, alice):
    from sqlalchemy import text

    from app.core.db import get_engine

    async with get_engine().begin() as conn:
        await conn.execute(text("UPDATE users SET is_active = false"))
    assert (await client.get("/api/v1/auth/me", headers=alice["headers"])).status_code == 401


async def test_audit_log_written(client, alice):
    from sqlalchemy import text

    from app.core.db import get_engine

    await client.post("/api/v1/persons", json={"display_name": "A"}, headers=alice["headers"])
    async with get_engine().connect() as conn:
        actions = {r[0] for r in await conn.execute(text("SELECT action FROM audit_logs"))}
    assert {"auth.register", "auth.login", "person.create"} <= actions
