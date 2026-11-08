import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.api.deps import CurrentUser, Session
from app.models import ConsentRecord, Person
from app.models.base import ConsentScope, ConsentStatus
from app.services import consent as consent_svc
from app.services.audit import audit

router = APIRouter(prefix="/privacy", tags=["privacy"])


class ConsentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    person_id: uuid.UUID
    scope: ConsentScope
    status: Literal["granted", "revoked"]
    method: str = Field(min_length=1, max_length=64)
    notice_text: str | None = Field(default=None, max_length=4000)


class ConsentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    person_id: uuid.UUID | None
    scope: ConsentScope
    status: ConsentStatus


@router.post("/consents", response_model=ConsentOut, status_code=status.HTTP_201_CREATED)
async def record(body: ConsentIn, user: CurrentUser, session: Session) -> ConsentRecord:
    owned = await session.scalar(select(Person.id).where(Person.id == body.person_id, Person.owner_id == user.id))
    if owned is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "person not found")
    rec = await consent_svc.record_consent(session, user.id, body.scope, ConsentStatus(body.status), body.method,
                                           person_id=body.person_id, notice_text=body.notice_text)
    await audit(session, owner_id=user.id, actor=f"user:{user.id}", action=f"consent.{body.status}",
                entity_type="person", entity_id=body.person_id, details={"scope": body.scope.value})
    return rec
