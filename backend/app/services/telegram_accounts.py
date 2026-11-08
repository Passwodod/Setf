import json
import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis import get_redis
from app.models import (
    Group, GroupMember, Message, MessageSource, Person, PersonIdentifier,
    TelegramAccount, TelegramImportRun, TelegramSource,
)
from app.models.base import ConsentScope, ConsentStatus
from app.security.crypto import decrypt_secret, encrypt_secret
from app.services import consent as consent_svc
from app.services import groups as groups_svc
from app.services.audit import audit
from app.services.mtproto import AuthRevoked, GatewayError, MTProtoGateway

log = logging.getLogger("sia.telegram")
PENDING_TTL = 600
MAX_IMPORT_BATCH = 500


class NotFound(Exception):
    pass


class ConsentMissing(Exception):
    pass


class InvalidPending(Exception):
    pass


def mask_phone(phone: str) -> str:
    digits = "".join(c for c in phone if c.isdigit())
    return f"+{'*' * max(len(digits) - 4, 0)}{digits[-4:]}"


# ---- connect / reauth -------------------------------------------------------------------------

async def start_connect(gw: MTProtoGateway, owner_id: uuid.UUID, *, label: str, api_id: int, api_hash: str,
                        phone: str, account_id: uuid.UUID | None) -> str:
    session_str, code_hash = await gw.send_code(api_id, api_hash, phone)
    pending_id = secrets.token_urlsafe(24)
    blob = json.dumps({"owner": str(owner_id), "label": label, "api_id": api_id, "api_hash": api_hash,
                       "phone": phone, "session": session_str, "hash": code_hash,
                       "account_id": str(account_id) if account_id else None})
    await get_redis().set(f"tgauth:{pending_id}", encrypt_secret(blob), ex=PENDING_TTL)
    return pending_id


async def finish_connect(session: AsyncSession, gw: MTProtoGateway, owner_id: uuid.UUID, pending_id: str, *,
                         code: str, password: str | None, actor: str) -> TelegramAccount:
    r = get_redis()
    raw = await r.get(f"tgauth:{pending_id}")
    if raw is None:
        raise InvalidPending
    data = json.loads(decrypt_secret(raw))
    if data["owner"] != str(owner_id):
        raise InvalidPending  # same response as unknown id: no oracle for other users' pending logins
    signed = await gw.sign_in(data["api_id"], data["api_hash"], data["session"], data["phone"], code,
                              data["hash"], password)
    await r.delete(f"tgauth:{pending_id}")  # code and 2FA password are never persisted
    now = datetime.now(UTC)
    account = None
    if data["account_id"]:
        account = await session.scalar(select(TelegramAccount).where(
            TelegramAccount.id == uuid.UUID(data["account_id"]), TelegramAccount.owner_id == owner_id))
    if account is None:
        account = await session.scalar(select(TelegramAccount).where(
            TelegramAccount.owner_id == owner_id, TelegramAccount.tg_user_id == signed.user_id))
    if account is None:
        account = TelegramAccount(owner_id=owner_id, label=data["label"], api_id=data["api_id"],
                                  api_hash_enc="", phone_masked=mask_phone(data["phone"]))
        session.add(account)
    account.api_id = data["api_id"]
    account.api_hash_enc = encrypt_secret(data["api_hash"])
    account.session_enc = encrypt_secret(signed.session)
    account.tg_user_id, account.tg_username = signed.user_id, signed.username
    account.phone_masked = mask_phone(data["phone"])
    account.status, account.last_error, account.connected_at = "connected", None, now
    await session.flush()
    await audit(session, owner_id=owner_id, actor=actor, action="telegram.account_connected",
                entity_type="telegram_account", entity_id=account.id)
    return account


async def get_account(session: AsyncSession, owner_id: uuid.UUID, account_id: uuid.UUID) -> TelegramAccount:
    acc = await session.scalar(select(TelegramAccount).where(
        TelegramAccount.id == account_id, TelegramAccount.owner_id == owner_id))
    if acc is None:
        raise NotFound
    return acc


def _creds(acc: TelegramAccount) -> tuple[int, str, str]:
    if not acc.session_enc or acc.status != "connected":
        raise AuthRevoked
    return acc.api_id, decrypt_secret(acc.api_hash_enc), decrypt_secret(acc.session_enc)


async def _mark_revoked(session: AsyncSession, acc: TelegramAccount) -> None:
    acc.status, acc.last_error = "needs_reauth", "Telegram session was revoked or expired"
    await session.flush()


async def list_dialogs(session: AsyncSession, gw: MTProtoGateway, acc: TelegramAccount):
    try:
        out = await gw.list_dialogs(*_creds(acc))
    except AuthRevoked:
        await _mark_revoked(session, acc)
        await session.commit()
        raise
    acc.last_used_at = datetime.now(UTC)
    return out


async def disconnect(session: AsyncSession, gw: MTProtoGateway, owner_id: uuid.UUID, acc: TelegramAccount, *,
                     delete_data: bool, actor: str) -> None:
    """Terminates the session on Telegram's side (best effort), wipes local ciphertext, and
    optionally erases everything imported through this account."""
    if acc.session_enc and acc.status == "connected":
        try:
            await gw.log_out(acc.api_id, decrypt_secret(acc.api_hash_enc), decrypt_secret(acc.session_enc))
        except Exception:  # remote logout failing must not keep the secret locally
            log.warning("remote logout failed for account %s", acc.id)
    acc.session_enc, acc.status, acc.last_error = None, "disconnected", None
    if delete_data:
        group_ids = [g for g in (await session.scalars(
            select(TelegramSource.group_id).where(TelegramSource.account_id == acc.id))).all() if g]
        if group_ids:
            await session.execute(Group.__table__.delete().where(Group.id.in_(group_ids), Group.owner_id == owner_id))
        await session.execute(TelegramSource.__table__.delete().where(TelegramSource.account_id == acc.id))
    await session.flush()
    await audit(session, owner_id=owner_id, actor=actor, action="telegram.account_disconnected",
                entity_type="telegram_account", entity_id=acc.id, details={"delete_data": delete_data})


# ---- sources ----------------------------------------------------------------------------------

async def add_source(session: AsyncSession, owner_id: uuid.UUID, acc: TelegramAccount, *, kind: str, peer_id: int,
                     title: str, person_id: uuid.UUID | None, members_notified: bool,
                     retention_days: int | None, backfill_limit: int, actor: str) -> TelegramSource:
    group = None
    if kind in ("group", "channel"):
        if not members_notified:
            raise ConsentMissing("members_notified must be confirmed before collecting a group or channel")
        group = await session.scalar(select(Group).where(Group.owner_id == owner_id, Group.telegram_chat_id == peer_id))
        if group is None:
            group = Group(owner_id=owner_id, telegram_chat_id=peer_id, title=title, status="active",
                          collection_enabled=True, notice_sent_at=datetime.now(UTC), retention_days=retention_days)
            session.add(group)
            await session.flush()
        await consent_svc.record_consent(session, owner_id, ConsentScope.GROUP_COLLECTION, ConsentStatus.GRANTED,
                                         "owner_attestation", group_id=group.id,
                                         notice_text="Owner attested that members were notified")
    else:
        if person_id is None:
            raise ConsentMissing("a person is required for contact/conversation sources")
        person = await session.scalar(select(Person).where(Person.id == person_id, Person.owner_id == owner_id))
        if person is None:
            raise NotFound
        # Adding a person is NOT consent; an explicit granted PROFILING record is required.
        if await consent_svc.current_consent(session, owner_id, ConsentScope.PROFILING, person_id=person_id) != ConsentStatus.GRANTED:
            raise ConsentMissing("no granted consent recorded for this person")
        group = await session.scalar(select(Group).where(Group.owner_id == owner_id, Group.telegram_chat_id == peer_id))
        if group is None:
            group = Group(owner_id=owner_id, telegram_chat_id=peer_id, title=title, status="active",
                          collection_enabled=True, notice_sent_at=datetime.now(UTC), retention_days=retention_days)
            session.add(group)
            await session.flush()
    src = TelegramSource(owner_id=owner_id, account_id=acc.id, kind=kind, peer_id=peer_id, title=title,
                         person_id=person_id, group_id=group.id, retention_days=retention_days,
                         backfill_limit=backfill_limit)
    session.add(src)
    await session.flush()
    await audit(session, owner_id=owner_id, actor=actor, action="telegram.source_added",
                entity_type="telegram_source", entity_id=src.id, details={"kind": kind})
    return src


async def get_source(session: AsyncSession, owner_id: uuid.UUID, account_id: uuid.UUID, source_id: uuid.UUID) -> TelegramSource:
    src = await session.scalar(select(TelegramSource).where(
        TelegramSource.id == source_id, TelegramSource.account_id == account_id, TelegramSource.owner_id == owner_id))
    if src is None:
        raise NotFound
    return src


# ---- import -----------------------------------------------------------------------------------

async def run_import(session: AsyncSession, gw: MTProtoGateway, owner_id: uuid.UUID, acc: TelegramAccount,
                     src: TelegramSource, *, actor: str) -> TelegramImportRun:
    run = TelegramImportRun(owner_id=owner_id, account_id=acc.id, source_id=src.id, status="running",
                            started_at=datetime.now(UTC))
    session.add(run)
    await session.flush()
    try:
        if not src.enabled or src.group_id is None:
            raise ConsentMissing("source is disabled")
        group = await session.get(Group, src.group_id)
        if group is None or not group.collection_enabled:
            raise ConsentMissing("collection is disabled for this source")
        if src.person_id and await consent_svc.current_consent(
                session, owner_id, ConsentScope.PROFILING, person_id=src.person_id) != ConsentStatus.GRANTED:
            raise ConsentMissing("consent was revoked")

        limit = min(src.backfill_limit if src.last_message_id is None else MAX_IMPORT_BATCH, MAX_IMPORT_BATCH)
        api_id, api_hash, sess = _creds(acc)
        msgs = await gw.fetch_messages(api_id, api_hash, sess, src.peer_id, src.last_message_id or 0, limit)
        run.fetched = len(msgs)

        ident = {r.value: r.person_id for r in (await session.scalars(
            select(PersonIdentifier).where(PersonIdentifier.owner_id == owner_id, PersonIdentifier.kind == "telegram_id")))}
        blocked = set((await session.scalars(
            select(GroupMember.person_id).where(GroupMember.group_id == group.id, GroupMember.excluded.is_(True)))).all())
        blocked |= set((await session.scalars(
            select(Person.id).where(Person.owner_id == owner_id, Person.do_not_profile.is_(True)))).all())
        days = src.retention_days or acc.retention_days or group.retention_days
        retained_until = datetime.now(UTC) + timedelta(days=days) if days else None

        for m in msgs:
            author = ident.get(str(m.sender_id)) if m.sender_id is not None else None
            if author in blocked or (m.sender_id is not None and await groups_svc.opted_out(session, owner_id, group.id, m.sender_id)):
                run.skipped += 1
                continue
            res = await session.execute(
                pg_insert(Message).values(owner_id=owner_id, group_id=group.id, author_person_id=author, author_telegram_id=m.sender_id,
                                          telegram_message_id=m.id, text=m.text, sent_at=m.date)
                .on_conflict_do_nothing(constraint="uq_messages_group_tgmsg").returning(Message.id))
            mid = res.scalar_one_or_none()
            if mid is None:
                run.duplicates += 1
                continue
            session.add(MessageSource(message_id=mid, source="mtproto", retained_until=retained_until))
            run.stored += 1
        if msgs:
            src.last_message_id = max(m.id for m in msgs)
        acc.last_used_at = datetime.now(UTC)
        run.status = "completed"
    except AuthRevoked:
        await _mark_revoked(session, acc)
        run.status, run.error = "failed", "session revoked; re-authorization required"
    except (ConsentMissing, GatewayError) as exc:
        run.status, run.error = "failed", str(exc) or type(exc).__name__
    run.finished_at = datetime.now(UTC)
    await audit(session, owner_id=owner_id, actor=actor, action="telegram.import", entity_type="telegram_source",
                entity_id=src.id, details={"status": run.status, "stored": run.stored})
    return run
