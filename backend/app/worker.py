"""Worker process: `python -m app.worker`. Safe to run several replicas."""
import asyncio
import logging
import signal
from datetime import UTC, datetime

from app.core.db import dispose_engine, get_sessionmaker
from app.core.redis import close_redis, get_redis
from app.jobs import handlers  # noqa: F401  (registers handlers)
from app.jobs import queue
from app.jobs.registry import HANDLER_TIMEOUT, REGISTRY, PermanentJobError
from app.models import BackgroundJob

log = logging.getLogger("sia.worker")


async def process(job_id) -> str:
    """Run one claimed job. Returns the resulting status."""
    async with get_sessionmaker()() as s:
        job = await s.get(BackgroundJob, job_id)
        if job is None or job.status in ("succeeded", "dead", "cancelled"):
            await queue.ack(job_id)
            return "skipped"
        job.status, job.attempts, job.started_at = "running", job.attempts + 1, datetime.now(UTC)
        await s.commit()
        fn = REGISTRY.get(job.kind)
        try:
            if fn is None:
                raise PermanentJobError(f"no handler for '{job.kind}'")
            result = await asyncio.wait_for(fn(s, job), HANDLER_TIMEOUT)
            job.status, job.result, job.finished_at, job.last_error = "succeeded", result, datetime.now(UTC), None
            await s.commit()
            await queue.ack(job_id)
            return "succeeded"
        except Exception as exc:
            await s.rollback()
            err = f"{type(exc).__name__}: {exc}"[:2000]
            job = await s.get(BackgroundJob, job_id)
            permanent = isinstance(exc, PermanentJobError)
            if permanent or job.attempts >= job.max_attempts:
                job.status, job.last_error, job.finished_at = "dead", err, datetime.now(UTC)
                await s.commit()
                await queue.bury(job_id)
                log.error("job %s (%s) dead: %s", job_id, job.kind, err)
                return "dead"
            delay = queue.backoff_seconds(job.attempts)
            job.status, job.last_error = "retrying", err
            await s.commit()
            await queue.schedule_retry(job_id, delay)
            log.warning("job %s (%s) attempt %s failed, retry in %.0fs: %s", job_id, job.kind, job.attempts, delay, err)
            return "retrying"


async def _schedule_periodic() -> None:
    hour = datetime.now(UTC).strftime("%Y%m%d%H")
    if await get_redis().set(f"sched:retention:{hour}", "1", nx=True, ex=7200):
        await queue.enqueue(None, "retention.purge", {}, idempotency_key=None)


async def run_worker(concurrency: int = 4, poll: float = 1.0, stop: asyncio.Event | None = None) -> None:
    stop = stop or asyncio.Event()
    sem = asyncio.Semaphore(concurrency)  # bounds parallel jobs per worker process
    running: set[asyncio.Task] = set()
    last_housekeeping = 0.0

    async def one(job_id):
        try:
            await process(job_id)
        except Exception:
            log.exception("worker crashed while processing %s", job_id)
        finally:
            sem.release()

    log.info("worker started (concurrency=%s)", concurrency)
    while not stop.is_set():
        now = asyncio.get_running_loop().time()
        if now - last_housekeeping > 15:
            last_housekeeping = now
            try:
                if (n := await queue.recover_stale()):
                    log.warning("re-queued %s stale job(s)", n)
                await _schedule_periodic()
            except Exception:
                log.exception("housekeeping failed")
        await sem.acquire()
        try:
            job_id = await queue.claim()
        except Exception:
            sem.release()
            log.exception("redis unavailable; retrying")
            await asyncio.sleep(poll * 3)
            continue
        if job_id is None:
            sem.release()
            try:
                await asyncio.wait_for(stop.wait(), poll)
            except TimeoutError:
                pass
            continue
        t = asyncio.create_task(one(job_id))
        running.add(t)
        t.add_done_callback(running.discard)
    if running:
        await asyncio.gather(*running, return_exceptions=True)
    log.info("worker stopped")


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    try:
        await run_worker(stop=stop)
    finally:
        await close_redis()
        await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
