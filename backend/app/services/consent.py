import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ConsentRecord
from app.models.base import ConsentScope, ConsentStatus


async def current_consent(
    session: AsyncSession, owner_id: uuid.UUID, scope: ConsentScope, *,
    person_id: uuid.UUID | None = None, group_id: uuid.UUID | None = None,
) -> ConsentStatus | None:
    """Latest ledger row wins; None means no consent has ever been recorded."""
    stmt = select(ConsentRecord.status).where(ConsentRecord.owner_id == owner_id, ConsentRecord.scope == scope)
    stmt = stmt.where(ConsentRecord.person_id == person_id) if person_id else stmt.where(ConsentRecord.group_id == group_id)
    stmt = stmt.order_by(ConsentRecord.recorded_at.desc(), ConsentRecord.id.desc()).limit(1)
    return await session.scalar(stmt)


async def record_consent(
    session: AsyncSession, owner_id: uuid.UUID, scope: ConsentScope, status: ConsentStatus, method: str, *,
    person_id: uuid.UUID | None = None, group_id: uuid.UUID | None = None, notice_text: str | None = None,
) -> ConsentRecord:
    rec = ConsentRecord(owner_id=owner_id, person_id=person_id, group_id=group_id, scope=scope,
                        status=status, method=method, notice_text=notice_text)
    session.add(rec)
    await session.flush()
    return rec
