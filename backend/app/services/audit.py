import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog

log = logging.getLogger("sia.audit")


async def audit(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID | None,
    actor: str,
    action: str,
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
    ip: str | None = None,
    details: dict | None = None,
) -> None:
    session.add(
        AuditLog(owner_id=owner_id, actor=actor, action=action, entity_type=entity_type,
                 entity_id=entity_id, ip=ip, details=details)
    )
    log.info("audit action=%s actor=%s entity=%s/%s", action, actor, entity_type, entity_id)
