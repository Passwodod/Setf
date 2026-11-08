"""Deterministic rule-based provider for development and automated tests.

It is NOT an AI model and its output must never be presented as one: `is_mock` is True, the model name is
"mock-rules-v1", it is stored on every record it produces, and the factory refuses it in production."""
import hashlib
import math
import re

from app.ai.concepts import CONCEPTS, match_concepts, skeleton
from app.ai.language import detect_language, interpret, tokens
from app.ai.providers.base import ProviderError, T
from app.ai.schemas import (
    Ambiguity, ConversationRequest, ConversationSuggestions, ExtractedFact, ExtractedInterest, ExtractionRequest,
    ExtractionResult, Suggestion,
)

FACT_PATTERNS = [
    (re.compile(r"\bi(?:'m| am) (?:a |an )?([a-z][a-z \-]{2,40}?(?:developer|engineer|designer|teacher|student|analyst|manager|doctor))\b", re.I), "occupation"),
    (re.compile(r"\bi work (?:as|at) (?:a |an )?([a-z][a-z0-9 \-&.]{2,40})", re.I), "occupation"),
    (re.compile(r"\bя работаю ([^\.,!?\n]{3,40})", re.I), "occupation"),
]

TEMPLATES = {
    "en": {"starter": "Hey! I remember you mentioned {t} – how is that going?", "question": "What got you into {t}?"},
    "ru": {"starter": "Привет! Ты упоминал(а) про {t} – как продвигается?", "question": "Как ты начал(а) заниматься темой «{t}»?"},
}


class MockProvider:
    name = "mock"
    model = "mock-rules-v1"
    embedding_model = "mock-hash-v1"
    embedding_dim = 1536
    is_mock = True

    async def structured(self, *, task: str, system: str, user: str, schema: type[T]) -> T:
        if schema is ExtractionResult:
            return self._extract(ExtractionRequest.model_validate_json(user))  # type: ignore[return-value]
        if schema is ConversationSuggestions:
            return self._suggest(ConversationRequest.model_validate_json(user))  # type: ignore[return-value]
        raise ProviderError(f"mock provider does not implement {schema.__name__}", transient=False)

    def _extract(self, req: ExtractionRequest) -> ExtractionResult:
        interests, facts, ambiguities = [], [], []
        langs: list[str] = []
        context = [m.text for m in req.messages if m.is_context]
        for m in req.messages:
            if m.is_context:
                continue
            langs.append(detect_language(m.text).code)
            quote = m.text.strip()[:500]
            for c in match_concepts(m.text):
                interests.append(ExtractedInterest(
                    name=c.concept.replace("_", " "), concept=c.concept, category=c.category, stance=c.stance,
                    message_index=m.index, quote=quote, confidence="low" if c.fuzzy or c.stance == "mention" else "medium"))
            for pat, field in FACT_PATTERNS:
                hit = pat.search(m.text)
                if hit:
                    facts.append(ExtractedFact(field=field, value=hit.group(1).strip(), self_reported=True,
                                               message_index=m.index, quote=quote, confidence="medium"))
                    break
            for a in interpret(m.text, context).ambiguous:
                ambiguities.append(Ambiguity(message_index=m.index, span=a.span, candidates=a.candidates))
        top = max(set(langs), key=langs.count) if langs else "und"
        return ExtractionResult(language=top, interests=interests, facts=facts, ambiguities=ambiguities)

    def _suggest(self, req: ConversationRequest) -> ConversationSuggestions:
        tpl = TEMPLATES.get(req.language, TEMPLATES["en"])
        out: list[Suggestion] = []
        for topic in req.topics:
            for kind in req.kinds:
                out.append(Suggestion(kind=kind, text=tpl[kind].format(t=topic.topic), topic=topic.topic))
        return ConversationSuggestions(language=req.language if req.language in TEMPLATES else "en", suggestions=out[: req.count])

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Hashing bag-of-tokens; recognised concepts add a shared language-independent feature."""
        out = []
        for t in texts:
            vec = [0.0] * self.embedding_dim
            feats = [skeleton(w) for w in tokens(t)] + [f"concept:{c.concept}" for c in match_concepts(t) for _ in range(3)]
            for f in feats:
                vec[int(hashlib.sha256(f.encode()).hexdigest(), 16) % self.embedding_dim] += 1.0
            norm = math.sqrt(sum(x * x for x in vec)) or 1.0
            out.append([x / norm for x in vec])
        return out
