import uuid

from pydantic import BaseModel, EmailStr, Field

from app.models.base import Role
from app.schemas.common import ORMModel


class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=256)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class RegisterRequest(Credentials):
    display_name: str = Field(default="", max_length=120)


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class UserOut(ORMModel):
    id: uuid.UUID
    email: EmailStr
    display_name: str
    role: Role
