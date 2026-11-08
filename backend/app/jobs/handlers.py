import logging
from datetime import UTC, datetime

from sqlalchemy import delete, select, update

from app.jobs.registry import PermanentJobError, handler, uid
from app.models import Evidence, Message, MessageSource, TelegramAccount, TelegramSource
from app.services import telegram_accounts as tg_svc
from app.services.mtproto import get_gateway

log = logging.getLogger("sia.jobs.handlers")


@handler("telegram.import")
async def telegram_import(session, job):
    if job.owner_id is None:
        raise PermanentJobError("import jobs need an owner")
    acc = await session.scalar(select(TelegramAccount).where(
        TelegramAccount.id == uid(job.payload, "account_id"), TelegramAccount.owner_id == job.owner_id))
    src = await session.scalar(select(TelegramSource).where(
        TelegramSource.id == uid(job.payload, "source_id"), TelegramSource.owner_id == job.owner_id))
    if acc is None or src is None or src.account_id != acc.id:
        raise PermanentJobError("account or source no longer exists")
    run = await tg_svc.run_import(session, get_gateway(), job.owner_id, acc, src, actor="worker")
    return {"run_id": str(run.id), "status": run.status, "stored": run.stored, "duplicates": run.duplicates,
            "skipped": run.skipped, "error": run.error}


@handler("retention.purge")
async def retention_purge(session, job):
    """Delete stored messages whose retention deadline passed, plus group-level retention."""
    now = datetime.now(UTC)
    expired = (await session.scalars(select(MessageSource.message_id).where(MessageSource.retained_until <= now))).all()
    if expired:
        await session.execute(update(Evidence).where(Evidence.message_id.in_(expired)).values(excerpt=None))
        await session.execute(delete(Message).where(Message.id.in_(expired)))
    return {"deleted_messages": len(expired)}
