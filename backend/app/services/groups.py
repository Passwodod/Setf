"""Authorized group collection (Bot API). Collection happens only after an explicit enable step that
follows a posted notice; members can opt out; everything can be viewed, stopped and purged."""
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    CollectionOptOut, Evidence, Group, GroupMember, Message, MessageSource, Person, PersonIdentifier,
)
from app.models.base import ConsentScope, ConsentStatus
from app.services import consent as consent_svc
from app.services.audit import audit

NOTICE = (
    "Notice: the owner of this bot has connected this group to SOCIAL INTELLIGENCE AI, an automated system. "
    "Once collection is enabled, text messages posted here are stored and analysed to organise information "
    "about participants' interests. Private chats are never collected.\n\n"
    "• /optout – exclude yourself: your stored messages here are deleted and future ones are ignored.\n"
    "• /optin – undo that.\n"
    "• The owner can stop collection or delete all data at any time."
)


class GroupNotFound(Exception):
    pass


class NoticeRequired(Exception):
    pass


async def get_group(session: AsyncSession, owner_id: uuid.UUID, group_id: uuid.UUID) -> Group:
    g = await session.scalar(select(Group).where(Group.id == group_id, Group.owner_id == owner_id))
    if g is None:
        raise GroupNotFound
    return g


async def group_by_chat(session: AsyncSession, chat_id: int) -> list[Group]:
    return list((await session.scalars(select(Group).where(Group.telegram_chat_id == chat_id))).all())


async def connect_group(session: AsyncSession, owner_id: uuid.UUID, chat_id: int, title: str,
                        bot_can_read_all: bool | None, actor: str) -> Group:
    g = await session.scalar(select(Group).where(Group.owner_id == owner_id, Group.telegram_chat_id == chat_id))
    if g is None:
        g = Group(owner_id=owner_id, telegram_chat_id=chat_id, title=title[:255], status="pending")
        session.add(g)
        await session.flush()
        await audit(session, owner_id=owner_id, actor=actor, action="group.connect", entity_type="group", entity_id=g.id)
    g.title, g.bot_can_read_all = title[:255], bot_can_read_all
    return g


async def mark_notice_sent(session: AsyncSession, g: Group) -> None:
    g.notice_sent_at = datetime.now(UTC)
    await session.flush()


async def enable_collection(session: AsyncSession, owner_id: uuid.UUID, g: Group, actor: str) -> None:
    if g.notice_sent_at is None:
        raise NoticeRequired("the notice must be posted in the group before collection can be enabled")
    g.collection_enabled, g.status = True, "active"
    await consent_svc.record_consent(session, owner_id, ConsentScope.GROUP_COLLECTION, ConsentStatus.GRANTED,
                                     "owner_attestation", group_id=g.id, notice_text=NOTICE)
    await audit(session, owner_id=owner_id, actor=actor, action="group.collection_enabled", entity_type="group", entity_id=g.id)


async def disable_collection(session: AsyncSession, owner_id: uuid.UUID, g: Group, actor: str) -> None:
    g.collection_enabled, g.status = False, "paused"
    await consent_svc.record_consent(session, owner_id, ConsentScope.GROUP_COLLECTION, ConsentStatus.REVOKED,
                                     "owner_action", group_id=g.id)
    await audit(session, owner_id=owner_id, actor=actor, action="group.collection_disabled", entity_type="group", entity_id=g.id)


async def _delete_messages(session: AsyncSession, where) -> int:
    ids = list((await session.scalars(select(Message.id).where(where))).all())
    if ids:
        await session.execute(update(Evidence).where(Evidence.message_id.in_(ids)).values(excerpt=None))
        await session.execute(delete(Message).where(Message.id.in_(ids)))
    return len(ids)


async def purge_messages(session: AsyncSession, owner_id: uuid.UUID, g: Group, actor: str) -> int:
    n = await _delete_messages(session, (Message.group_id == g.id) & (Message.owner_id == owner_id))
    await audit(session, owner_id=owner_id, actor=actor, action="group.purge", entity_type="group", entity_id=g.id,
                details={"deleted_messages": n})
    return n


async def delete_group(session: AsyncSession, owner_id: uuid.UUID, g: Group, actor: str) -> None:
    await _delete_messages(session, (Message.group_id == g.id) & (Message.owner_id == owner_id))
    gid = g.id
    await session.delete(g)
    await audit(session, owner_id=owner_id, actor=actor, action="group.delete", entity_type="group", entity_id=gid)


async def opted_out(session: AsyncSession, owner_id: uuid.UUID, group_id: uuid.UUID, tg_user_id: int) -> bool:
    return bool(await session.scalar(select(CollectionOptOut.id).where(
        CollectionOptOut.owner_id == owner_id, CollectionOptOut.telegram_user_id == tg_user_id,
        (CollectionOptOut.group_id == group_id) | CollectionOptOut.group_id.is_(None))))


async def opt_out(session: AsyncSession, g: Group, tg_user_id: int) -> int:
    """Exclude a member and delete what was already stored for them in this group."""
    await session.execute(pg_insert(CollectionOptOut).values(
        owner_id=g.owner_id, group_id=g.id, telegram_user_id=tg_user_id).on_conflict_do_nothing())
    person_ids = select(PersonIdentifier.person_id).where(
        PersonIdentifier.owner_id == g.owner_id, PersonIdentifier.kind == "telegram_id",
        PersonIdentifier.value == str(tg_user_id))
    deleted = await _delete_messages(session, (Message.group_id == g.id) & (
        (Message.author_telegram_id == tg_user_id) | Message.author_person_id.in_(person_ids)))
    await audit(session, owner_id=g.owner_id, actor="bot", action="group.member_opt_out", entity_type="group",
                entity_id=g.id, details={"deleted_messages": deleted})
    return deleted


async def opt_in(session: AsyncSession, g: Group, tg_user_id: int) -> None:
    await session.execute(delete(CollectionOptOut).where(
        CollectionOptOut.owner_id == g.owner_id, CollectionOptOut.group_id == g.id,
        CollectionOptOut.telegram_user_id == tg_user_id))


async def ingest(session: AsyncSession, g: Group, *, tg_message_id: int, tg_user_id: int | None, text: str,
                 sent_at: datetime, source: str = "telegram_webhook") -> str:
    """Store one message if policy allows. Returns stored|duplicate|skipped. Idempotent on redelivery."""
    if not g.collection_enabled or tg_user_id is None or not text.strip():
        return "skipped"
    if await opted_out(session, g.owner_id, g.id, tg_user_id):
        return "skipped"
    author = await session.scalar(select(PersonIdentifier.person_id).where(
        PersonIdentifier.owner_id == g.owner_id, PersonIdentifier.kind == "telegram_id",
        PersonIdentifier.value == str(tg_user_id)))
    if author is not None:
        person = await session.get(Person, author)
        excluded = await session.scalar(select(GroupMember.id).where(
            GroupMember.group_id == g.id, GroupMember.person_id == author, GroupMember.excluded.is_(True)))
        if (person and person.do_not_profile) or excluded:
            return "skipped"
    res = await session.execute(
        pg_insert(Message).values(owner_id=g.owner_id, group_id=g.id, author_person_id=author,
                                  author_telegram_id=tg_user_id, telegram_message_id=tg_message_id,
                                  text=text[:8000], sent_at=sent_at)
        .on_conflict_do_nothing(constraint="uq_messages_group_tgmsg").returning(Message.id))
    mid = res.scalar_one_or_none()
    if mid is None:
        return "duplicate"
    days = g.retention_days
    session.add(MessageSource(message_id=mid, source=source,
                              retained_until=datetime.now(UTC) + timedelta(days=days) if days else None))
    return "stored"


async def stats(session: AsyncSession, owner_id: uuid.UUID, g: Group) -> dict:
    n = await session.scalar(select(func.count()).select_from(Message).where(Message.group_id == g.id)) or 0
    return {"message_count": n}
