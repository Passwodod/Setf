"""Provider interface. Anything that can return schema-valid JSON and embeddings can be plugged in."""
from typing import Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)
PROMPT_VERSION = "v1"


class ProviderError(Exception):
    def __init__(self, message: str, transient: bool):
        super().__init__(message)
        self.transient = transient  # transient errors are retried by the job queue


class ProviderNotConfigured(ProviderError):
    def __init__(self, message: str):
        super().__init__(message, transient=False)


class LLMProvider(Protocol):
    name: str
    model: str
    embedding_model: str
    embedding_dim: int
    is_mock: bool

    async def structured(self, *, task: str, system: str, user: str, schema: type[T]) -> T:
        """Return `schema`-valid output. `task` names the operation (extraction, conversation, ...)."""
        ...

    async def embed(self, texts: list[str]) -> list[list[float]]: ...
