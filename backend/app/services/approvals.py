import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Approval, Conversation, ConversationMessage, InteractionEvent, Person
from app.models.base import ApprovalStatus, ConsentScope, ConsentStatus
from app.services import consent as consent_svc
from app.services.audit import audit
from app.services.telegram_send import SendError, TextSender

log = logging.getLogger("sia.approvals")
TTL = timedelta(hours=24)
MAX_LEN = 4096


class ApprovalNotFound(Exception):
    pass


class NotEligible(Exception):
    """Recipient cannot be messaged (no consent / never opened the bot)."""


class InvalidState(Exception):
    pass


async def check_eligible(session: AsyncSession, owner_id: uuid.UUID, person: Person) -> None:
    if person.bot_chat_id is None:
        raise NotEligible("recipient has not opened the bot")
    status = await consent_svc.current_consent(session, owner_id, ConsentScope.DIRECT_MESSAGING, person_id=person.id)
    if status != ConsentStatus.GRANTED:
        raise NotEligible("recipient has not granted consent to receive messages")


async def get_approval(session: AsyncSession, owner_id: uuid.UUID, approval_id: uuid.UUID) -> Approval:
    a = await session.scalar(select(Approval).where(Approval.id == approval_id, Approval.owner_id == owner_id))
    if a is None:
        raise ApprovalNotFound
    return a


def _clean(text: str) -> str:
    text = text.strip()
    if not 1 <= len(text) <= MAX_LEN:
        raise ValueError("text must be 1-4096 characters")
    return text


async def create_approval(session: AsyncSession, owner_id: uuid.UUID, person_id: uuid.UUID, text: str, actor: str) -> Approval:
    text = _clean(text)
    person = await session.scalar(select(Person).where(Person.id == person_id, Person.owner_id == owner_id))
    if person is None:
        raise ApprovalNotFound
    await check_eligible(session, owner_id, person)
    a = Approval(owner_id=owner_id, person_id=person_id, text=text, status=ApprovalStatus.PENDING,
                 expires_at=datetime.now(UTC) + TTL)
    session.add(a)
    await session.flush()
    await audit(session, owner_id=owner_id, actor=actor, action="approval.create", entity_type="approval", entity_id=a.id)
    return a


async def expire_stale(session: AsyncSession, owner_id: uuid.UUID) -> None:
    await session.execute(update(Approval).where(
        Approval.owner_id == owner_id, Approval.status == ApprovalStatus.PENDING, Approval.expires_at <= datetime.now(UTC)
    ).values(status=ApprovalStatus.EXPIRED))


async def edit_text(session: AsyncSession, owner_id: uuid.UUID, approval_id: uuid.UUID, text: str, actor: str) -> Approval:
    text = _clean(text)
    res = await session.execute(update(Approval).where(
        Approval.id == approval_id, Approval.owner_id == owner_id, Approval.status == ApprovalStatus.PENDING,
        Approval.expires_at > datetime.now(UTC),
    ).values(text=text, revision=Approval.revision + 1).returning(Approval.id))
    if res.scalar_one_or_none() is None:
        await get_approval(session, owner_id, approval_id)
        raise InvalidState("approval is no longer pending")
    await audit(session, owner_id=owner_id, actor=actor, action="approval.edit", entity_type="approval", entity_id=approval_id)
    a = await get_approval(session, owner_id, approval_id)
    await session.refresh(a)
    return a


async def _finish_unsent(session, owner_id, approval_id, status: ApprovalStatus, actor: str, action: str) -> Approval:
    res = await session.execute(update(Approval).where(
        Approval.id == approval_id, Approval.owner_id == owner_id, Approval.status == ApprovalStatus.PENDING
    ).values(status=status, decided_at=datetime.now(UTC)).returning(Approval.id))
    if res.scalar_one_or_none() is None:
        await get_approval(session, owner_id, approval_id)
        raise InvalidState("approval is no longer pending")
    await audit(session, owner_id=owner_id, actor=actor, action=action, entity_type="approval", entity_id=approval_id)
    a = await get_approval(session, owner_id, approval_id)
    await session.refresh(a)
    return a


async def reject(session, owner_id, approval_id, actor) -> Approval:
    return await _finish_unsent(session, owner_id, approval_id, ApprovalStatus.REJECTED, actor, "approval.reject")


async def cancel(session, owner_id, approval_id, actor) -> Approval:
    return await _finish_unsent(session, owner_id, approval_id, ApprovalStatus.CANCELLED, actor, "approval.cancel")


async def approve_and_send(session: AsyncSession, sender: TextSender, owner_id: uuid.UUID, approval_id: uuid.UUID,
                           actor: str) -> Approval:
    """Exactly-once send. The pending->sending claim is one conditional UPDATE committed before the
    network call, so a double click, a retry, or two workers cannot both win it."""
    now = datetime.now(UTC)
    res = await session.execute(update(Approval).where(
        Approval.id == approval_id, Approval.owner_id == owner_id, Approval.status == ApprovalStatus.PENDING,
        Approval.expires_at > now,
    ).values(status=ApprovalStatus.SENDING, decided_at=now, send_attempts=Approval.send_attempts + 1)
        .returning(Approval.id))
    if res.scalar_one_or_none() is None:
        a = await get_approval(session, owner_id, approval_id)
        if a.status == ApprovalStatus.PENDING and a.expires_at <= now:
            a.status = ApprovalStatus.EXPIRED
            await session.flush()
            raise InvalidState("approval expired")
        raise InvalidState(f"approval is {a.status.value}")
    await session.commit()  # claim is durable before anything is sent

    a = await get_approval(session, owner_id, approval_id)
    person = await session.get(Person, a.person_id)
    try:
        await check_eligible(session, owner_id, person)  # consent may have been revoked since creation
        tg_id = await sender.send_text(person.bot_chat_id, a.text)
    except (NotEligible, SendError) as exc:
        a.status, a.last_error = ApprovalStatus.FAILED, str(exc)
        await audit(session, owner_id=owner_id, actor=actor, action="approval.send_failed", entity_type="approval",
                    entity_id=a.id, details={"error": str(exc)})
        await session.commit()
        return a
    except Exception as exc:  # unknown outcome: never auto-retry, a human must check
        log.exception("unexpected send error for approval %s", a.id)
        a.status, a.last_error = ApprovalStatus.FAILED, f"unexpected error: {type(exc).__name__}"
        await session.commit()
        return a

    a.status, a.sent_at, a.telegram_message_id = ApprovalStatus.SENT, datetime.now(UTC), tg_id
    conv = await session.scalar(select(Conversation).where(
        Conversation.owner_id == owner_id, Conversation.person_id == person.id, Conversation.status == "open"))
    if conv is None:
        conv = Conversation(owner_id=owner_id, person_id=person.id)
        session.add(conv)
        await session.flush()
    a.conversation_id = conv.id
    session.add(ConversationMessage(owner_id=owner_id, conversation_id=conv.id, direction="outbound", text=a.text,
                                    approval_id=a.id, telegram_message_id=tg_id))
    session.add(InteractionEvent(owner_id=owner_id, person_id=person.id, kind="message_sent",
                                 payload={"approval_id": str(a.id)}))
    await audit(session, owner_id=owner_id, actor=actor, action="approval.sent", entity_type="approval", entity_id=a.id)
    await session.commit()
    return a
