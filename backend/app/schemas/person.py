import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import ORMModel


class PersonBase(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    display_name: str = Field(min_length=1, max_length=200)
    category: str | None = Field(default=None, max_length=64)
    birth_year: int | None = Field(default=None, ge=1900, le=2100)
    languages: list[str] = Field(default_factory=list, max_length=20)
    country: str | None = Field(default=None, max_length=80)
    city: str | None = Field(default=None, max_length=120)
    education: str | None = Field(default=None, max_length=2000)
    notes: str | None = Field(default=None, max_length=10000)
    do_not_profile: bool = False


class PersonCreate(PersonBase):
    telegram_username: str | None = Field(default=None, pattern=r"^@?[A-Za-z0-9_]{3,64}$")


class PersonUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    category: str | None = Field(default=None, max_length=64)
    birth_year: int | None = Field(default=None, ge=1900, le=2100)
    languages: list[str] | None = Field(default=None, max_length=20)
    country: str | None = Field(default=None, max_length=80)
    city: str | None = Field(default=None, max_length=120)
    education: str | None = Field(default=None, max_length=2000)
    notes: str | None = Field(default=None, max_length=10000)
    do_not_profile: bool | None = None


class PersonOut(ORMModel):
    id: uuid.UUID
    display_name: str
    category: str | None
    birth_year: int | None
    languages: list[str]
    country: str | None
    city: str | None
    education: str | None
    notes: str | None
    do_not_profile: bool
    created_at: datetime
    updated_at: datetime
