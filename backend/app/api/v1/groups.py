import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from app.api.deps import CurrentUser, Session
from app.models import Group, Message
from app.schemas.common import ORMModel, Page
from app.services import groups as svc

router = APIRouter(prefix="/groups", tags=["groups"])


class GroupOut(ORMModel):
    id: uuid.UUID
    telegram_chat_id: int
    title: str
    status: str
    collection_enabled: bool
    notice_sent_at: datetime | None
    retention_days: int | None
    bot_can_read_all: bool | None
    created_at: datetime


class GroupPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    collection_enabled: bool | None = None
    retention_days: int | None = Field(default=None, gt=0, le=3650)


class MessageOut(ORMModel):
    id: uuid.UUID
    author_person_id: uuid.UUID | None
    text: str
    sent_at: datetime


async def _group(session, user, group_id) -> Group:
    try:
        return await svc.get_group(session, user.id, group_id)
    except svc.GroupNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "group not found") from None


@router.get("", response_model=Page[GroupOut])
async def list_groups(user: CurrentUser, session: Session, limit: Annotated[int, Query(ge=1, le=100)] = 25,
                      offset: Annotated[int, Query(ge=0)] = 0):
    base = select(Group).where(Group.owner_id == user.id)
    total = await session.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = (await session.scalars(base.order_by(Group.title).limit(limit).offset(offset))).all()
    return Page(items=[GroupOut.model_validate(r) for r in rows], total=total, limit=limit, offset=offset)


@router.get("/{group_id}", response_model=GroupOut)
async def get_group(group_id: uuid.UUID, user: CurrentUser, session: Session) -> Group:
    return await _group(session, user, group_id)


@router.patch("/{group_id}", response_model=GroupOut)
async def patch_group(group_id: uuid.UUID, body: GroupPatch, user: CurrentUser, session: Session) -> Group:
    g = await _group(session, user, group_id)
    actor = f"user:{user.id}"
    if body.retention_days is not None:
        g.retention_days = body.retention_days
    if body.collection_enabled is True and not g.collection_enabled:
        try:
            await svc.enable_collection(session, user.id, g, actor)
        except svc.NoticeRequired as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    elif body.collection_enabled is False and g.collection_enabled:
        await svc.disable_collection(session, user.id, g, actor)
    await session.flush()
    await session.refresh(g)
    return g


@router.get("/{group_id}/messages", response_model=Page[MessageOut])
async def stored_messages(group_id: uuid.UUID, user: CurrentUser, session: Session,
                          limit: Annotated[int, Query(ge=1, le=100)] = 25, offset: Annotated[int, Query(ge=0)] = 0):
    """What is stored about this group (data transparency)."""
    g = await _group(session, user, group_id)
    base = select(Message).where(Message.group_id == g.id, Message.owner_id == user.id)
    total = await session.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = (await session.scalars(base.order_by(Message.sent_at.desc()).limit(limit).offset(offset))).all()
    return Page(items=[MessageOut.model_validate(r) for r in rows], total=total, limit=limit, offset=offset)


@router.post("/{group_id}/purge")
async def purge(group_id: uuid.UUID, user: CurrentUser, session: Session) -> dict:
    g = await _group(session, user, group_id)
    return {"deleted_messages": await svc.purge_messages(session, user.id, g, f"user:{user.id}")}


@router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_group(group_id: uuid.UUID, user: CurrentUser, session: Session) -> Response:
    g = await _group(session, user, group_id)
    await svc.delete_group(session, user.id, g, f"user:{user.id}")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
