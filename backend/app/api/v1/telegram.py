import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select

from app.api.deps import CurrentUser, Session
from app.core.ratelimit import enforce_rate_limit
from app.models import TelegramAccount, TelegramImportRun, TelegramSource
from app.schemas.telegram import (
    AccountOut, AccountPatch, ConnectStart, ConnectStartOut, ConnectVerify, DialogOut, ImportRunOut,
    SessionSecurityOut, SourceCreate, SourceOut, SourcePatch,
)
from app.security.crypto import CryptoError
from app.services import telegram_accounts as svc
from app.services.mtproto import (
    AuthRevoked, CodeInvalid, GatewayError, MTProtoGateway, PasswordRequired, get_gateway,
)

router = APIRouter(prefix="/telegram", tags=["telegram"])
Gateway = Annotated[MTProtoGateway, Depends(get_gateway)]


def _nf(_: Exception | None = None) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "not found")


async def _acc(session, user, account_id) -> TelegramAccount:
    try:
        return await svc.get_account(session, user.id, account_id)
    except svc.NotFound:
        raise _nf() from None


@router.post("/accounts/connect/start", response_model=ConnectStartOut)
async def connect_start(body: ConnectStart, user: CurrentUser, session: Session, gw: Gateway) -> ConnectStartOut:
    await enforce_rate_limit("tg-connect", str(user.id), 5)
    if body.account_id:
        await _acc(session, user, body.account_id)
    try:
        pending = await svc.start_connect(gw, user.id, label=body.label, api_id=body.api_id,
                                          api_hash=body.api_hash.get_secret_value(), phone=body.phone,
                                          account_id=body.account_id)
    except CryptoError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "session encryption is not configured") from None
    except GatewayError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"telegram error: {exc}") from None
    return ConnectStartOut(pending_id=pending, expires_in=svc.PENDING_TTL)


@router.post("/accounts/connect/verify", response_model=AccountOut, status_code=status.HTTP_201_CREATED)
async def connect_verify(body: ConnectVerify, user: CurrentUser, session: Session, gw: Gateway) -> TelegramAccount:
    await enforce_rate_limit("tg-verify", str(user.id), 10)
    try:
        return await svc.finish_connect(session, gw, user.id, body.pending_id, code=body.code,
                                        password=body.password.get_secret_value() if body.password else None,
                                        actor=f"user:{user.id}")
    except svc.InvalidPending:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "pending login not found or expired") from None
    except PasswordRequired:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "two-factor password required") from None
    except CodeInvalid:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid or expired code") from None
    except GatewayError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"telegram error: {exc}") from None


@router.get("/accounts", response_model=list[AccountOut])
async def list_accounts(user: CurrentUser, session: Session) -> list[TelegramAccount]:
    rows = await session.scalars(select(TelegramAccount).where(TelegramAccount.owner_id == user.id)
                                 .order_by(TelegramAccount.created_at))
    return list(rows)


@router.get("/accounts/{account_id}", response_model=AccountOut)
async def get_account(account_id: uuid.UUID, user: CurrentUser, session: Session) -> TelegramAccount:
    return await _acc(session, user, account_id)


@router.patch("/accounts/{account_id}", response_model=AccountOut)
async def patch_account(account_id: uuid.UUID, body: AccountPatch, user: CurrentUser, session: Session) -> TelegramAccount:
    acc = await _acc(session, user, account_id)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(acc, k, v)
    await session.flush()
    await session.refresh(acc)
    return acc


@router.get("/accounts/{account_id}/security", response_model=SessionSecurityOut)
async def session_security(account_id: uuid.UUID, user: CurrentUser, session: Session) -> SessionSecurityOut:
    acc = await _acc(session, user, account_id)
    return SessionSecurityOut(encrypted_at_rest=True, has_session=acc.session_enc is not None, status=acc.status,
                              connected_at=acc.connected_at, last_used_at=acc.last_used_at,
                              capabilities=["read_selected_sources"])


@router.delete("/accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_account(account_id: uuid.UUID, user: CurrentUser, session: Session, gw: Gateway,
                             delete_data: bool = False) -> Response:
    acc = await _acc(session, user, account_id)
    await svc.disconnect(session, gw, user.id, acc, delete_data=delete_data, actor=f"user:{user.id}")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/accounts/{account_id}/dialogs", response_model=list[DialogOut])
async def dialogs(account_id: uuid.UUID, user: CurrentUser, session: Session, gw: Gateway):
    acc = await _acc(session, user, account_id)
    try:
        return await svc.list_dialogs(session, gw, acc)
    except AuthRevoked:
        raise HTTPException(status.HTTP_409_CONFLICT, "re-authorization required") from None
    except GatewayError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"telegram error: {exc}") from None


@router.get("/accounts/{account_id}/sources", response_model=list[SourceOut])
async def list_sources(account_id: uuid.UUID, user: CurrentUser, session: Session) -> list[TelegramSource]:
    await _acc(session, user, account_id)
    return list(await session.scalars(select(TelegramSource).where(
        TelegramSource.account_id == account_id, TelegramSource.owner_id == user.id)))


@router.post("/accounts/{account_id}/sources", response_model=SourceOut, status_code=status.HTTP_201_CREATED)
async def add_source(account_id: uuid.UUID, body: SourceCreate, user: CurrentUser, session: Session) -> TelegramSource:
    acc = await _acc(session, user, account_id)
    dup = await session.scalar(select(TelegramSource.id).where(
        TelegramSource.account_id == account_id, TelegramSource.peer_id == body.peer_id))
    if dup:
        raise HTTPException(status.HTTP_409_CONFLICT, "source already added")
    try:
        return await svc.add_source(session, user.id, acc, kind=body.kind, peer_id=body.peer_id, title=body.title,
                                    person_id=body.person_id, members_notified=body.members_notified,
                                    retention_days=body.retention_days, backfill_limit=body.backfill_limit,
                                    actor=f"user:{user.id}")
    except svc.ConsentMissing as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    except svc.NotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "person not found") from None


@router.patch("/accounts/{account_id}/sources/{source_id}", response_model=SourceOut)
async def patch_source(account_id: uuid.UUID, source_id: uuid.UUID, body: SourcePatch, user: CurrentUser, session: Session):
    try:
        src = await svc.get_source(session, user.id, account_id, source_id)
    except svc.NotFound:
        raise _nf() from None
    for k, v in body.model_dump(exclude_unset=True).items():
        if v is not None or k == "retention_days":
            setattr(src, k, v)
    await session.flush()
    await session.refresh(src)
    return src


@router.delete("/accounts/{account_id}/sources/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_source(account_id: uuid.UUID, source_id: uuid.UUID, user: CurrentUser, session: Session) -> Response:
    try:
        src = await svc.get_source(session, user.id, account_id, source_id)
    except svc.NotFound:
        raise _nf() from None
    await session.delete(src)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/accounts/{account_id}/sources/{source_id}/import", response_model=ImportRunOut)
async def import_source(account_id: uuid.UUID, source_id: uuid.UUID, user: CurrentUser, session: Session, gw: Gateway):
    await enforce_rate_limit("tg-import", str(user.id), 20)
    acc = await _acc(session, user, account_id)
    try:
        src = await svc.get_source(session, user.id, account_id, source_id)
    except svc.NotFound:
        raise _nf() from None
    return await svc.run_import(session, gw, user.id, acc, src, actor=f"user:{user.id}")


@router.post("/accounts/{account_id}/sources/{source_id}/import/background", status_code=status.HTTP_202_ACCEPTED)
async def import_source_background(account_id: uuid.UUID, source_id: uuid.UUID, user: CurrentUser, session: Session) -> dict:
    """Queue the import for the worker; poll GET /api/v1/jobs/{id}. Repeated clicks reuse the pending job."""
    from datetime import UTC, datetime

    from app.jobs import queue

    await enforce_rate_limit("tg-import", str(user.id), 20)
    await _acc(session, user, account_id)
    try:
        await svc.get_source(session, user.id, account_id, source_id)
    except svc.NotFound:
        raise _nf() from None
    bucket = datetime.now(UTC).strftime("%Y%m%d%H%M")  # one queued import per source per minute
    job, created = await queue.enqueue(user.id, "telegram.import", {"account_id": str(account_id), "source_id": str(source_id)},
                                       idempotency_key=f"{source_id}:{bucket}")
    return {"job_id": str(job.id), "status": job.status, "created": created}


@router.get("/accounts/{account_id}/imports", response_model=list[ImportRunOut])
async def import_history(account_id: uuid.UUID, user: CurrentUser, session: Session,
                         limit: Annotated[int, Query(ge=1, le=100)] = 25) -> list[TelegramImportRun]:
    await _acc(session, user, account_id)
    return list(await session.scalars(select(TelegramImportRun).where(
        TelegramImportRun.account_id == account_id, TelegramImportRun.owner_id == user.id)
        .order_by(TelegramImportRun.started_at.desc()).limit(limit)))
