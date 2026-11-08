"""Structured schemas exchanged with any LLM provider. Messages are referenced by integer `index` so a
model can never invent a message id, and every claim must carry a verbatim `quote` that is checked."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Confidence = Literal["low", "medium", "high"]
Stance = Literal["interest", "mention", "negative"]
FactField = Literal["occupation", "employer", "specialty", "skill", "industry", "goal", "technology", "financial_interest"]


class _S(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InMessage(_S):
    index: int
    text: str
    is_context: bool = False  # context-only messages help disambiguate and must not be cited as evidence


class ExtractionRequest(_S):
    messages: list[InMessage]
    subject_hint: str = "the author of the non-context messages"


class ExtractedInterest(_S):
    name: str = Field(min_length=1, max_length=120, description="Concise topic name in English")
    concept: str | None = Field(default=None, description="Canonical concept slug if one applies")
    category: str = Field(default="other", max_length=48)
    stance: Stance
    message_index: int
    quote: str = Field(min_length=1, max_length=500, description="Verbatim excerpt from that message")
    confidence: Confidence


class ExtractedFact(_S):
    field: FactField
    value: str = Field(min_length=1, max_length=300)
    self_reported: bool = Field(description="True only if the author states it about themselves")
    message_index: int
    quote: str = Field(min_length=1, max_length=500)
    confidence: Confidence


class ExtractedProject(_S):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    message_index: int
    quote: str = Field(min_length=1, max_length=500)


class Ambiguity(_S):
    message_index: int
    span: str
    candidates: list[str]


class ExtractionResult(_S):
    language: str = Field(description="Dominant language code, 'mixed' or 'und'")
    interests: list[ExtractedInterest] = []
    facts: list[ExtractedFact] = []
    projects: list[ExtractedProject] = []
    ambiguities: list[Ambiguity] = []


class TopicBrief(_S):
    topic: str
    reasons: list[str] = []


class ConversationRequest(_S):
    language: str
    topics: list[TopicBrief]
    avoid: list[str] = []
    kinds: list[Literal["starter", "question"]] = ["starter", "question"]
    count: int = Field(default=5, ge=1, le=10)


class Suggestion(_S):
    kind: Literal["starter", "question"]
    text: str = Field(min_length=1, max_length=400)
    topic: str = Field(description="Must be one of the provided topics")


class ConversationSuggestions(_S):
    language: str
    suggestions: list[Suggestion]
