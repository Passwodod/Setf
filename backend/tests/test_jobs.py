import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from app.core.db import get_engine, get_sessionmaker
from app.core.redis import get_redis
from app.jobs import queue
from app.jobs.registry import REGISTRY, PermanentJobError, handler
from app.models import BackgroundJob
from app.worker import process, run_worker
from tests.conftest import make_user
from tests.test_telegram import FakeGateway, connect, msg


@pytest.fixture(autouse=True)
def fast_backoff(monkeypatch):
    monkeypatch.setattr(queue, "backoff_seconds", lambda attempt: 0.0)
    monkeypatch.setattr("app.worker.queue.backoff_seconds", lambda attempt: 0.0, raising=False)
    yield
    for k in [k for k in REGISTRY if k.startswith("test.")]:
        del REGISTRY[k]


async def user_id(client):
    u = await make_user(client, "w@example.com")
    me = (await client.get("/api/v1/auth/me", headers=u["headers"])).json()
    return uuid.UUID(me["id"]), u


async def run_until_idle(max_loops=20):
    """Drain the queue deterministically (single worker, no sleeping)."""
    done = []
    for _ in range(max_loops):
        jid = await queue.claim()
        if jid is None:
            break
        done.append((jid, await process(jid)))
    return done


async def all_done(jobs):
    return all([(await status_of(j.id)).status == "succeeded" for j in jobs])


async def status_of(jid):
    async with get_sessionmaker()() as s:
        return await s.get(BackgroundJob, jid)


async def test_enqueue_is_idempotent(client):
    uid, _ = await user_id(client)
    a, created_a = await queue.enqueue(uid, "test.noop", {}, idempotency_key="k1")
    b, created_b = await queue.enqueue(uid, "test.noop", {}, idempotency_key="k1")
    assert created_a and not created_b and a.id == b.id
    assert await get_redis().zcard(queue.READY) == 1


async def test_job_runs_and_records_result(client):
    uid, _ = await user_id(client)

    @handler("test.ok")
    async def ok(session, job):
        return {"echo": job.payload["x"]}

    job, _ = await queue.enqueue(uid, "test.ok", {"x": 7})
    assert await run_until_idle() == [(job.id, "succeeded")]
    row = await status_of(job.id)
    assert row.status == "succeeded" and row.result == {"echo": 7} and row.attempts == 1
    assert await get_redis().zcard(queue.PROCESSING) == 0


async def test_claim_is_exclusive(client):
    uid, _ = await user_id(client)
    await queue.enqueue(uid, "test.noop", {})
    got = await asyncio.gather(*[queue.claim() for _ in range(10)])
    assert len([g for g in got if g]) == 1


async def test_retry_with_backoff_then_dead_letter(client):
    uid, _ = await user_id(client)
    calls = []

    @handler("test.flaky")
    async def flaky(session, job):
        calls.append(1)
        raise RuntimeError("boom")

    job, _ = await queue.enqueue(uid, "test.flaky", {}, max_attempts=3)
    results = await run_until_idle()
    assert [s for _, s in results] == ["retrying", "retrying", "dead"] and len(calls) == 3
    row = await status_of(job.id)
    assert row.status == "dead" and "boom" in row.last_error
    assert await get_redis().lrange(queue.DEAD, 0, -1) == [str(job.id)]


async def test_job_succeeds_after_transient_failure(client):
    uid, _ = await user_id(client)
    state = {"n": 0}

    @handler("test.eventually")
    async def eventually(session, job):
        state["n"] += 1
        if state["n"] < 3:
            raise ConnectionError("temporary")
        return {"n": state["n"]}

    job, _ = await queue.enqueue(uid, "test.eventually", {})
    await run_until_idle()
    row = await status_of(job.id)
    assert row.status == "succeeded" and row.attempts == 3


async def test_permanent_error_skips_retries(client):
    uid, _ = await user_id(client)

    @handler("test.perm")
    async def perm(session, job):
        raise PermanentJobError("bad input")

    job, _ = await queue.enqueue(uid, "test.perm", {}, max_attempts=5)
    assert [s for _, s in await run_until_idle()] == ["dead"]
    assert (await status_of(job.id)).attempts == 1


async def test_unknown_kind_goes_dead(client):
    uid, _ = await user_id(client)
    job, _ = await queue.enqueue(uid, "test.nobody_handles_this", {})
    assert [s for _, s in await run_until_idle()] == ["dead"]


async def test_delayed_job_not_claimable_early(client):
    uid, _ = await user_id(client)
    await queue.enqueue(uid, "test.noop", {}, delay=3600)
    assert await queue.claim() is None
    assert (await queue.depth())["delayed"] == 1


async def test_crashed_worker_job_is_recovered(client):
    uid, _ = await user_id(client)
    job, _ = await queue.enqueue(uid, "test.noop", {})
    assert await queue.claim() == job.id  # worker "dies" holding it
    assert await queue.claim() is None
    await get_redis().zadd(queue.PROCESSING, {str(job.id): 1})  # visibility timeout long past
    assert await queue.recover_stale() == 1
    assert await queue.claim() == job.id


async def test_run_worker_loop_processes_and_stops(client):
    uid, _ = await user_id(client)

    @handler("test.loop")
    async def loop_job(session, job):
        return {"ok": True}

    jobs = [(await queue.enqueue(uid, "test.loop", {"i": i}))[0] for i in range(6)]
    stop = asyncio.Event()
    task = asyncio.create_task(run_worker(concurrency=3, poll=0.05, stop=stop))
    for _ in range(100):
        if await all_done(jobs):
            break
        await asyncio.sleep(0.05)
    stop.set()
    await asyncio.wait_for(task, 5)
    assert await all_done(jobs)


async def test_concurrency_limit_respected(client):
    uid, _ = await user_id(client)
    active = {"now": 0, "max": 0}

    @handler("test.slow")
    async def slow(session, job):
        active["now"] += 1
        active["max"] = max(active["max"], active["now"])
        await asyncio.sleep(0.1)
        active["now"] -= 1

    jobs = [(await queue.enqueue(uid, "test.slow", {"i": i}))[0] for i in range(8)]
    stop = asyncio.Event()
    task = asyncio.create_task(run_worker(concurrency=2, poll=0.02, stop=stop))
    for _ in range(200):
        if await all_done(jobs):
            break
        await asyncio.sleep(0.05)
    stop.set()
    await asyncio.wait_for(task, 5)
    assert active["max"] == 2


# ---- API -------------------------------------------------------------------------------------
async def test_jobs_api_isolation_retry_cancel(client):
    uid, alice = await user_id(client)
    other = await make_user(client, "o@example.com")

    @handler("test.dead")
    async def dead(session, job):
        raise PermanentJobError("nope")

    job, _ = await queue.enqueue(uid, "test.dead", {})
    await run_until_idle()
    h = alice["headers"]
    assert (await client.get(f"/api/v1/jobs/{job.id}", headers=h)).json()["status"] == "dead"
    assert (await client.get(f"/api/v1/jobs/{job.id}", headers=other["headers"])).status_code == 404
    assert (await client.post(f"/api/v1/jobs/{job.id}/retry", headers=other["headers"])).status_code == 404
    assert (await client.get("/api/v1/jobs", headers=other["headers"])).json()["total"] == 0
    st = (await client.get("/api/v1/jobs/stats", headers=h)).json()
    assert st["by_status"] == {"dead": 1} and st["queue"]["dead"] == 1
    assert (await client.get("/api/v1/jobs/stats", headers=other["headers"])).json()["queue"] is None
    r = await client.post(f"/api/v1/jobs/{job.id}/retry", headers=h)
    assert r.status_code == 200 and r.json()["status"] == "queued"
    assert (await client.post(f"/api/v1/jobs/{job.id}/cancel", headers=h)).json()["status"] == "cancelled"
    assert await queue.claim() is None
    assert (await client.post(f"/api/v1/jobs/{job.id}/retry", headers=h)).status_code == 409
    assert (await client.get("/api/v1/jobs")).status_code == 401


async def test_background_import_end_to_end(client, monkeypatch):
    from app.services import mtproto

    gw = FakeGateway()
    monkeypatch.setattr(mtproto, "_gateway", gw)
    client._transport.app.dependency_overrides[mtproto.get_gateway] = lambda: gw
    uid, alice = await user_id(client)
    h = alice["headers"]
    acc = await connect(client, h)
    base = f"/api/v1/telegram/accounts/{acc['id']}"
    src = (await client.post(f"{base}/sources", headers=h, json={"kind": "group", "peer_id": -77, "title": "G", "members_notified": True})).json()
    gw.messages[-77] = [msg(i) for i in range(1, 6)]
    r1 = await client.post(f"{base}/sources/{src['id']}/import/background", headers=h)
    r2 = await client.post(f"{base}/sources/{src['id']}/import/background", headers=h)
    assert r1.status_code == 202 and r1.json()["created"] and not r2.json()["created"]
    assert r1.json()["job_id"] == r2.json()["job_id"]
    async with get_engine().connect() as c:
        assert (await c.execute(text("SELECT count(*) FROM messages"))).scalar() == 0  # nothing happened in the request
    await run_until_idle()
    job = (await client.get(f"/api/v1/jobs/{r1.json()['job_id']}", headers=h)).json()
    assert job["status"] == "succeeded" and job["result"]["stored"] == 5
    assert (await client.post(f"/api/v1/telegram/accounts/{acc['id']}/sources/{uuid.uuid4()}/import/background", headers=h)).status_code == 404


async def test_retention_purge_removes_expired_messages_and_excerpts(client):
    uid, _ = await user_id(client)
    async with get_engine().begin() as c:
        gid, m1, m2, pid, oid = (uuid.uuid4() for _ in range(5))
        await c.execute(text("INSERT INTO groups(id,owner_id,telegram_chat_id,title) VALUES (:g,:o,-1,'G')"), {"g": gid, "o": uid})
        await c.execute(text("INSERT INTO persons(id,owner_id,display_name) VALUES (:p,:o,'P')"), {"p": pid, "o": uid})
        for m, tgid, until in ((m1, 1, "now() - interval '1 day'"), (m2, 2, "now() + interval '30 days'")):
            await c.execute(text("INSERT INTO messages(id,owner_id,group_id,telegram_message_id,text,sent_at) VALUES (:m,:o,:g,:t,'secret text',now())"), {"m": m, "o": uid, "g": gid, "t": tgid})
            await c.execute(text(f"INSERT INTO message_sources(id,message_id,source,retained_until) VALUES (gen_random_uuid(),:m,'test',{until})"), {"m": m})
        await c.execute(text("INSERT INTO observations(id,owner_id,person_id,category,claim,source_type,status,confidence) VALUES (:i,:o,:p,'interest','c','ai_inference','INFERRED','low')"), {"i": oid, "o": uid, "p": pid})
        await c.execute(text("INSERT INTO evidence(id,owner_id,observation_id,message_id,excerpt) VALUES (gen_random_uuid(),:o,:i,:m,'secret text')"), {"o": uid, "i": oid, "m": m1})
    job, _ = await queue.enqueue(None, "retention.purge", {})
    await run_until_idle()
    assert (await status_of(job.id)).result == {"deleted_messages": 1}
    async with get_engine().connect() as c:
        assert (await c.execute(text("SELECT count(*) FROM messages"))).scalar() == 1
        assert (await c.execute(text("SELECT excerpt FROM evidence"))).scalar() is None
