import os
import subprocess
import sys

os.environ["SIA_DATABASE_URL"] = "postgresql+asyncpg://sia:sia_dev_pw@localhost:5432/sia_test"
os.environ["SIA_REDIS_URL"] = "redis://localhost:6379/15"
os.environ["SIA_JWT_SECRET"] = "test-secret-test-secret-test-secret-0123"
os.environ["SIA_ALLOW_REGISTRATION"] = "true"
from cryptography.fernet import Fernet  # noqa: E402
os.environ["SIA_SESSION_ENCRYPTION_KEYS"] = Fernet.generate_key().decode()
os.environ["SIA_RATE_LIMIT_LOGIN_PER_MINUTE"] = "1000"

import httpx  # noqa: E402
import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.core.db import dispose_engine, get_engine  # noqa: E402
from app.core.redis import close_redis, get_redis  # noqa: E402
from app.main import create_app  # noqa: E402

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="session", autouse=True)
def migrated_db():
    """Recreate the schema through Alembic so tests exercise the real migrations."""
    env = dict(os.environ)
    run = lambda *a: subprocess.run([sys.executable, "-m", "alembic", *a], cwd=BACKEND_DIR, env=env, check=True)  # noqa: E731
    reset = subprocess.run(
        ["psql", "-h", "localhost", "-U", "sia", "-d", "sia_test", "-v", "ON_ERROR_STOP=1", "-c",
         "DROP OWNED BY sia;"],
        env={**env, "PGPASSWORD": "sia_dev_pw"}, capture_output=True, text=True,
    )
    assert reset.returncode == 0, reset.stderr
    run("upgrade", "head")


@pytest_asyncio.fixture(autouse=True)
async def clean(migrated_db):
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE users CASCADE"))
    await get_redis().flushdb()
    yield


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _shutdown():
    yield
    await close_redis()
    await dispose_engine()


@pytest_asyncio.fixture
async def client():
    app = create_app()
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def make_user(client: httpx.AsyncClient, email: str, password: str = "correct-horse-battery") -> dict:
    r = await client.post("/api/v1/auth/register", json={"email": email, "password": password})
    assert r.status_code == 201, r.text
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    tokens = r.json()
    return {"headers": {"Authorization": f"Bearer {tokens['access_token']}"}, "tokens": tokens}


@pytest_asyncio.fixture
async def alice(client):
    return await make_user(client, "alice@example.com")


@pytest_asyncio.fixture
async def bob(client, alice):
    return await make_user(client, "bob@example.com")
