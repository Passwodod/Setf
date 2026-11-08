import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select

from app.api.deps import CurrentUser, Session
from app.jobs import queue
from app.models import BackgroundJob
from app.models.base import Role
from app.schemas.common import ORMModel, Page

router = APIRouter(prefix="/jobs", tags=["jobs"])


class JobOut(ORMModel):
    id: uuid.UUID
    kind: str
    status: str
    attempts: int
    max_attempts: int
    run_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    last_error: str | None
    result: dict | None


class JobStats(BaseModel):
    by_status: dict[str, int]
    queue: dict[str, int] | None  # global queue depth, administrators only


async def _own(session, user, job_id) -> BackgroundJob:
    job = await session.scalar(select(BackgroundJob).where(BackgroundJob.id == job_id, BackgroundJob.owner_id == user.id))
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found")
    return job


@router.get("", response_model=Page[JobOut])
async def list_jobs(user: CurrentUser, session: Session, status_: Annotated[str | None, Query(alias="status")] = None,
                    kind: Annotated[str | None, Query(max_length=64)] = None,
                    limit: Annotated[int, Query(ge=1, le=100)] = 25, offset: Annotated[int, Query(ge=0)] = 0):
    stmt = select(BackgroundJob).where(BackgroundJob.owner_id == user.id)
    if status_:
        stmt = stmt.where(BackgroundJob.status == status_)
    if kind:
        stmt = stmt.where(BackgroundJob.kind == kind)
    total = await session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = (await session.scalars(stmt.order_by(BackgroundJob.created_at.desc()).limit(limit).offset(offset))).all()
    return Page(items=[JobOut.model_validate(r) for r in rows], total=total, limit=limit, offset=offset)


@router.get("/stats", response_model=JobStats)
async def stats(user: CurrentUser, session: Session) -> JobStats:
    rows = await session.execute(select(BackgroundJob.status, func.count()).where(
        BackgroundJob.owner_id == user.id).group_by(BackgroundJob.status))
    return JobStats(by_status={s: n for s, n in rows.all()}, queue=await queue.depth() if user.role == Role.ADMIN else None)


@router.get("/{job_id}", response_model=JobOut)
async def get_job(job_id: uuid.UUID, user: CurrentUser, session: Session) -> BackgroundJob:
    return await _own(session, user, job_id)


@router.post("/{job_id}/retry", response_model=JobOut)
async def retry(job_id: uuid.UUID, user: CurrentUser, session: Session) -> BackgroundJob:
    job = await _own(session, user, job_id)
    if job.status != "dead":
        raise HTTPException(status.HTTP_409_CONFLICT, "only dead jobs can be retried")
    await queue.requeue_dead(session, job)
    return job


@router.post("/{job_id}/cancel", response_model=JobOut)
async def cancel(job_id: uuid.UUID, user: CurrentUser, session: Session) -> BackgroundJob:
    job = await _own(session, user, job_id)
    if not await queue.cancel(session, job):
        raise HTTPException(status.HTTP_409_CONFLICT, "job is not cancellable")
    await session.refresh(job)
    return job
