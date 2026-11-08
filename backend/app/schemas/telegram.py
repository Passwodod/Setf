import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.schemas.common import ORMModel


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConnectStart(_Strict):
    label: str = Field(min_length=1, max_length=80)
    api_id: int = Field(gt=0)
    api_hash: SecretStr = Field(min_length=16, max_length=64)
    phone: str = Field(pattern=r"^\+\d{7,15}$")
    account_id: uuid.UUID | None = None  # set to re-authorize an existing account


class ConnectStartOut(BaseModel):
    pending_id: str
    expires_in: int


class ConnectVerify(_Strict):
    pending_id: str = Field(max_length=64)
    code: str = Field(min_length=3, max_length=10, pattern=r"^\d+$")
    password: SecretStr | None = Field(default=None, max_length=256)


class AccountOut(ORMModel):
    id: uuid.UUID
    label: str
    phone_masked: str
    tg_user_id: int | None
    tg_username: str | None
    status: str
    last_error: str | None
    retention_days: int | None
    connected_at: datetime | None
    last_used_at: datetime | None


class AccountPatch(_Strict):
    label: str | None = Field(default=None, min_length=1, max_length=80)
    retention_days: int | None = Field(default=None, gt=0, le=3650)


class SessionSecurityOut(BaseModel):
    encrypted_at_rest: bool
    has_session: bool
    status: str
    connected_at: datetime | None
    last_used_at: datetime | None
    capabilities: list[str]


class DialogOut(BaseModel):
    peer_id: int
    title: str
    kind: str


class SourceCreate(_Strict):
    kind: Literal["group", "channel", "contact", "conversation"]
    peer_id: int
    title: str = Field(min_length=1, max_length=255)
    person_id: uuid.UUID | None = None
    members_notified: bool = False
    retention_days: int | None = Field(default=None, gt=0, le=3650)
    backfill_limit: int = Field(default=200, ge=0, le=5000)


class SourcePatch(_Strict):
    enabled: bool | None = None
    retention_days: int | None = Field(default=None, gt=0, le=3650)
    backfill_limit: int | None = Field(default=None, ge=0, le=5000)


class SourceOut(ORMModel):
    id: uuid.UUID
    account_id: uuid.UUID
    kind: str
    peer_id: int
    title: str
    person_id: uuid.UUID | None
    enabled: bool
    retention_days: int | None
    backfill_limit: int
    last_message_id: int | None


class ImportRunOut(ORMModel):
    id: uuid.UUID
    source_id: uuid.UUID | None
    status: str
    fetched: int
    stored: int
    duplicates: int
    skipped: int
    error: str | None
    started_at: datetime
    finished_at: datetime | None
