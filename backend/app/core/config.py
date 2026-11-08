from functools import lru_cache

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="SIA_", extra="ignore")

    env: str = "development"
    database_url: str = "postgresql+asyncpg://sia:sia_dev_pw@localhost:5432/sia"
    redis_url: str = "redis://localhost:6379/0"
    jwt_secret: SecretStr = Field(default=SecretStr(""))
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 30
    refresh_token_days: int = 14
    allow_registration: bool = False
    rate_limit_login_per_minute: int = 10
    cors_origins: list[str] = []
    openai_api_key: SecretStr = Field(default=SecretStr(""))
    telegram_bot_token: SecretStr = Field(default=SecretStr(""))
    telegram_webhook_secret: SecretStr = Field(default=SecretStr(""))
    embedding_dim: int = 1536
    session_encryption_keys: SecretStr = Field(default=SecretStr(""))  # comma-separated Fernet keys, newest first

    @model_validator(mode="after")
    def _require_secret(self):
        secret = self.jwt_secret.get_secret_value()
        if not secret or len(secret) < 32:
            raise ValueError("SIA_JWT_SECRET must be set to at least 32 characters")
        if self.env == "production" and "dev" in secret.lower():
            raise ValueError("SIA_JWT_SECRET looks like a development value")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
