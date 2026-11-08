import logging
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from app.api.deps import CurrentUser, Session
from app.bot.factory import get_bot
from app.models import Approval
from app.models.base import ApprovalStatus
from app.schemas.common import ORMModel, Page
from app.services import approvals as svc
from app.services.telegram_send import AiogramSender, TextSender

router = APIRouter(prefix="/approvals", tags=["approvals"])
log = logging.getLogger("sia.approvals.api")


def get_sender(bot=Depends(get_bot)) -> TextSender:
    if bot is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "bot is not configured")
    return AiogramSender(bot)


class ApprovalCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    person_id: uuid.UUID
    text: str = Field(min_length=1, max_length=4096)


class ApprovalEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=4096)


class ApprovalOut(ORMModel):
    id: uuid.UUID
    person_id: uuid.UUID
    text: str
    status: ApprovalStatus
    revision: int
    expires_at: datetime
    decided_at: datetime | None
    sent_at: datetime | None
    last_error: str | None


def _map(exc: Exception) -> HTTPException:
    if isinstance(exc, svc.ApprovalNotFound):
        return HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    if isinstance(exc, svc.NotEligible):
        return HTTPException(status.HTTP_409_CONFLICT, str(exc))
    if isinstance(exc, svc.InvalidState):
        return HTTPException(status.HTTP_409_CONFLICT, str(exc))
    return HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))


@router.post("", response_model=ApprovalOut, status_code=status.HTTP_201_CREATED)
async def create(body: ApprovalCreate, user: CurrentUser, session: Session, bot=Depends(get_bot)) -> Approval:
    try:
        a = await svc.create_approval(session, user.id, body.person_id, body.text, f"user:{user.id}")
    except (svc.ApprovalNotFound, svc.NotEligible, ValueError) as exc:
        raise _map(exc) from None
    if bot is not None and user.telegram_user_id:
        from app.bot.handlers.approvals import notify_admin

        try:
            await notify_admin(bot, session, a, user.telegram_user_id)
        except Exception:
            log.warning("could not notify admin about approval %s", a.id)
    return a


@router.get("", response_model=Page[ApprovalOut])
async def list_approvals(user: CurrentUser, session: Session, status_: Annotated[ApprovalStatus | None, Query(alias="status")] = None,
                         limit: Annotated[int, Query(ge=1, le=100)] = 25, offset: Annotated[int, Query(ge=0)] = 0):
    await svc.expire_stale(session, user.id)
    stmt = select(Approval).where(Approval.owner_id == user.id)
    if status_:
        stmt = stmt.where(Approval.status == status_)
    total = await session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = (await session.scalars(stmt.order_by(Approval.created_at.desc()).limit(limit).offset(offset))).all()
    return Page(items=[ApprovalOut.model_validate(r) for r in rows], total=total, limit=limit, offset=offset)


@router.get("/{approval_id}", response_model=ApprovalOut)
async def get(approval_id: uuid.UUID, user: CurrentUser, session: Session) -> Approval:
    try:
        return await svc.get_approval(session, user.id, approval_id)
    except svc.ApprovalNotFound as exc:
        raise _map(exc) from None


@router.patch("/{approval_id}", response_model=ApprovalOut)
async def edit(approval_id: uuid.UUID, body: ApprovalEdit, user: CurrentUser, session: Session) -> Approval:
    try:
        return await svc.edit_text(session, user.id, approval_id, body.text, f"user:{user.id}")
    except (svc.ApprovalNotFound, svc.InvalidState, ValueError) as exc:
        raise _map(exc) from None


@router.post("/{approval_id}/send", response_model=ApprovalOut)
async def send(approval_id: uuid.UUID, user: CurrentUser, session: Session, sender: TextSender = Depends(get_sender)) -> Approval:
    try:
        return await svc.approve_and_send(session, sender, user.id, approval_id, f"user:{user.id}")
    except (svc.ApprovalNotFound, svc.InvalidState) as exc:
        raise _map(exc) from None


@router.post("/{approval_id}/reject", response_model=ApprovalOut)
async def reject(approval_id: uuid.UUID, user: CurrentUser, session: Session) -> Approval:
    try:
        return await svc.reject(session, user.id, approval_id, f"user:{user.id}")
    except (svc.ApprovalNotFound, svc.InvalidState) as exc:
        raise _map(exc) from None


@router.post("/{approval_id}/cancel", response_model=ApprovalOut)
async def cancel(approval_id: uuid.UUID, user: CurrentUser, session: Session) -> Approval:
    try:
        return await svc.cancel(session, user.id, approval_id, f"user:{user.id}")
    except (svc.ApprovalNotFound, svc.InvalidState) as exc:
        raise _map(exc) from None
