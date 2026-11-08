from tests.conftest import make_user


async def test_register_login_me(client):
    u = await make_user(client, "a@example.com")
    r = await client.get("/api/v1/auth/me", headers=u["headers"])
    assert r.status_code == 200
    assert r.json()["email"] == "a@example.com"
    assert r.json()["role"] == "admin"  # first user bootstraps as admin
    assert "password" not in r.text


async def test_second_user_is_not_admin(client, alice, bob):
    r = await client.get("/api/v1/auth/me", headers=bob["headers"])
    assert r.json()["role"] == "owner"


async def test_duplicate_email_case_insensitive(client, alice):
    r = await client.post("/api/v1/auth/register", json={"email": "ALICE@example.com", "password": "another-long-password"})
    assert r.status_code == 409


async def test_weak_password_rejected(client):
    r = await client.post("/api/v1/auth/register", json={"email": "x@example.com", "password": "short"})
    assert r.status_code == 422


async def test_wrong_password_uniform_error(client, alice):
    bad_pw = await client.post("/api/v1/auth/login", json={"email": "alice@example.com", "password": "nope-nope-nope"})
    no_user = await client.post("/api/v1/auth/login", json={"email": "ghost@example.com", "password": "nope-nope-nope"})
    assert bad_pw.status_code == no_user.status_code == 401
    assert bad_pw.json() == no_user.json()


async def test_refresh_and_logout_revokes_tokens(client, alice):
    r = await client.post("/api/v1/auth/refresh", json={"refresh_token": alice["tokens"]["refresh_token"]})
    assert r.status_code == 200
    assert (await client.post("/api/v1/auth/logout", headers=alice["headers"])).status_code == 204
    assert (await client.get("/api/v1/auth/me", headers=alice["headers"])).status_code == 401
    r = await client.post("/api/v1/auth/refresh", json={"refresh_token": alice["tokens"]["refresh_token"]})
    assert r.status_code == 401


async def test_registration_closed_after_bootstrap_when_disabled(client, alice, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "allow_registration", False)
    r = await client.post("/api/v1/auth/register", json={"email": "c@example.com", "password": "correct-horse-battery"})
    assert r.status_code == 403


async def test_login_rate_limited(client, alice, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "rate_limit_login_per_minute", 3)
    codes = [
        (await client.post("/api/v1/auth/login", json={"email": "alice@example.com", "password": "wrong-wrong-wrong"})).status_code
        for _ in range(5)
    ]
    assert codes[0] == 401 and codes[-1] == 429
