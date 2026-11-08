"""Redis-backed job queue.

Redis keys
  jobs:ready       ZSET  job_id -> run_at (epoch). Due members are claimable; future ones are delayed/backing off.
  jobs:processing  ZSET  job_id -> visibility deadline. Entries past the deadline (crashed worker) are re-queued.
  jobs:dead        LIST  job_ids that exhausted retries (the dead-letter queue); rows keep the error.
Claiming is a single Lua script so two workers can never take the same job.
"""
import logging
import random
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_sessionmaker
from app.core.redis import get_redis
from app.models import BackgroundJob

log = logging.getLogger("sia.jobs")
READY, PROCESSING, DEAD = "jobs:ready", "jobs:processing", "jobs:dead"
VISIBILITY_SECONDS = 300
BACKOFF_BASE, BACKOFF_CAP = 5.0, 900.0

_CLAIM = """
local ids = redis.call('ZRANGEBYSCORE', KEYS[1], '-inf', ARGV[1], 'LIMIT', 0, 1)
if #ids == 0 then return false end
redis.call('ZREM', KEYS[1], ids[1])
redis.call('ZADD', KEYS[2], ARGV[2], ids[1])
return ids[1]
"""
_RECOVER = """
local ids = redis.call('ZRANGEBYSCORE', KEYS[2], '-inf', ARGV[1])
for _, id in ipairs(ids) do
  redis.call('ZREM', KEYS[2], id)
  redis.call('ZADD', KEYS[1], ARGV[1], id)
end
return #ids
"""


def backoff_seconds(attempt: int) -> float:
    """Exponential backoff with jitter: ~5s, 10s, 20s ... capped at 15 minutes."""
    base = min(BACKOFF_CAP, BACKOFF_BASE * (2 ** max(attempt - 1, 0)))
    return base * random.uniform(0.8, 1.2)


def _now() -> float:
    return datetime.now(UTC).timestamp()


async def enqueue(owner_id: uuid.UUID | None, kind: str, payload: dict, *, idempotency_key: str | None = None,
                  delay: float = 0, max_attempts: int = 5) -> tuple[BackgroundJob, bool]:
    """Create the job row (own transaction, committed before Redis is told) and schedule it.
    With an idempotency key, a repeat returns the existing job and created=False."""
    run_at = datetime.now(UTC) + timedelta(seconds=delay)
    async with get_sessionmaker()() as s:
        stmt = pg_insert(BackgroundJob).values(
            owner_id=owner_id, kind=kind, payload=payload, status="queued", run_at=run_at,
            max_attempts=max_attempts, idempotency_key=idempotency_key,
        ).on_conflict_do_nothing(constraint="uq_background_jobs_idem").returning(BackgroundJob.id)
        job_id = (await s.execute(stmt)).scalar_one_or_none()
        created = job_id is not None
        if job_id is None:
            job_id = await s.scalar(select(BackgroundJob.id).where(
                BackgroundJob.owner_id == owner_id, BackgroundJob.kind == kind,
                BackgroundJob.idempotency_key == idempotency_key))
        await s.commit()
        job = await s.get(BackgroundJob, job_id)
    if created:
        await get_redis().zadd(READY, {str(job_id): run_at.timestamp()})
    return job, created


async def claim() -> uuid.UUID | None:
    now = _now()
    got = await get_redis().eval(_CLAIM, 2, READY, PROCESSING, now, now + VISIBILITY_SECONDS)
    return uuid.UUID(got) if got else None


async def recover_stale() -> int:
    return int(await get_redis().eval(_RECOVER, 2, READY, PROCESSING, _now()))


async def ack(job_id: uuid.UUID) -> None:
    await get_redis().zrem(PROCESSING, str(job_id))


async def schedule_retry(job_id: uuid.UUID, delay: float) -> None:
    r = get_redis()
    await r.zrem(PROCESSING, str(job_id))
    await r.zadd(READY, {str(job_id): _now() + delay})


async def bury(job_id: uuid.UUID) -> None:
    r = get_redis()
    await r.zrem(PROCESSING, str(job_id))
    await r.lpush(DEAD, str(job_id))


async def requeue_dead(session: AsyncSession, job: BackgroundJob) -> None:
    job.status, job.attempts, job.last_error, job.run_at = "queued", 0, None, datetime.now(UTC)
    await session.flush()
    await session.commit()
    r = get_redis()
    await r.lrem(DEAD, 0, str(job.id))
    await r.zadd(READY, {str(job.id): _now()})


async def cancel(session: AsyncSession, job: BackgroundJob) -> bool:
    res = await session.execute(update(BackgroundJob).where(
        BackgroundJob.id == job.id, BackgroundJob.status.in_(("queued", "retrying"))).values(status="cancelled")
        .returning(BackgroundJob.id))
    ok = res.scalar_one_or_none() is not None
    if ok:
        await session.commit()
        await get_redis().zrem(READY, str(job.id))
    return ok


async def depth() -> dict[str, int]:
    r = get_redis()
    now = _now()
    return {"ready": await r.zcount(READY, "-inf", now), "delayed": await r.zcount(READY, f"({now}", "+inf"),
            "processing": await r.zcard(PROCESSING), "dead": await r.llen(DEAD)}
