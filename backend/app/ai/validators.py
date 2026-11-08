"""Trust boundary for model output. Nothing from a provider reaches the database without passing here."""
import re
from dataclasses import dataclass, field

from app.ai.language import normalize
from app.ai.schemas import ExtractionRequest, ExtractionResult

# Sensitive characteristics must never be inferred or stored from chat, regardless of what a model returns.
SENSITIVE = re.compile(
    r"(health|illness|disease|diagnos|depress|anxiety|mental|therapy|suicid|cancer|hiv|pregnan|disabilit|"
    r"religio|faith|church|mosque|islam|christian|muslim|jewish|atheis|"
    r"politic|election|party member|democrat|republican|communis|nationalis|"
    r"sexual|gay|lesbian|lgbt|orientation|ethnic|race\b|racial|"
    r"salary|income|net worth|wealth|rich\b|poor\b|bank account|credit card|debt|"
    r"здоровь|болезн|диагноз|депресс|религи|вер[ауе] в бога|церков|мечет|политик|выбор[ыов]|партия|"
    r"ориентаци|национальност|зарплат|доход|богат|бедн|долг|"
    r"денсаулық|дін\b|саясат|жалақы|кіріс|саламаттык|дин\b|саясат|айлык)",
    re.IGNORECASE,
)
CONF_ORDER = {"low": 0, "medium": 1, "high": 2}
MAX_AI_CONFIDENCE = "medium"  # a model alone never earns "high"


@dataclass
class Rejection:
    kind: str
    reason: str
    item: str


@dataclass
class Validated:
    result: ExtractionResult
    rejected: list[Rejection] = field(default_factory=list)


def _squash(s: str) -> str:
    return re.sub(r"\s+", " ", normalize(s)).strip()


def _cap(conf: str) -> str:
    return conf if CONF_ORDER[conf] <= CONF_ORDER[MAX_AI_CONFIDENCE] else MAX_AI_CONFIDENCE


def validate_extraction(req: ExtractionRequest, res: ExtractionResult) -> Validated:
    by_index = {m.index: m for m in req.messages if not m.is_context}
    rejected: list[Rejection] = []

    def grounded(kind: str, label: str, idx: int, quote: str) -> bool:
        msg = by_index.get(idx)
        if msg is None:
            rejected.append(Rejection(kind, "message_index is not an evidence message", label))
            return False
        if _squash(quote) not in _squash(msg.text):
            rejected.append(Rejection(kind, "quote is not a verbatim excerpt of the message", label))
            return False
        return True

    def sensitive(kind: str, label: str, *texts: str) -> bool:
        if any(SENSITIVE.search(t or "") for t in texts):
            rejected.append(Rejection(kind, "sensitive characteristic", label))
            return True
        return False

    interests = []
    seen = set()
    for i in res.interests:
        if not grounded("interest", i.name, i.message_index, i.quote) or sensitive("interest", i.name, i.name, i.category, i.concept or ""):
            continue
        key = (i.concept or i.name.lower(), i.message_index, i.stance)
        if key in seen:
            continue
        seen.add(key)
        interests.append(i.model_copy(update={"confidence": _cap(i.confidence)}))

    facts = []
    for f in res.facts:
        if not grounded("fact", f"{f.field}={f.value}", f.message_index, f.quote) or sensitive("fact", f.value, f.value):
            continue
        if f.field == "financial_interest" and re.search(r"\d", f.value):
            rejected.append(Rejection("fact", "financial figures are not stored", f.value))
            continue
        # "self_reported" must be backed by a first-person marker in the quote; otherwise it is only inferred
        first_person = re.search(r"\b(i|i'm|i am|my|me|we|our)\b|\b(я|мой|моя|мое|мне|мы|наш)\b|\b(мен|менің|мен)\b", _squash(f.quote))
        facts.append(f.model_copy(update={"confidence": _cap(f.confidence),
                                          "self_reported": bool(f.self_reported and first_person)}))

    projects = [p for p in res.projects if grounded("project", p.name, p.message_index, p.quote)
                and not sensitive("project", p.name, p.name, p.description or "")]
    ambiguities = [a for a in res.ambiguities if a.message_index in by_index]
    clean = ExtractionResult(language=res.language, interests=interests, facts=facts, projects=projects,
                             ambiguities=ambiguities)
    return Validated(clean, rejected)


# ---- conversation output ---------------------------------------------------------------------
SOLICITATION = re.compile(
    r"(password|passcode|\bpin\b|cvv|card number|iban|bank account|sms code|one-time|verification code|secret|"
    r"social security|passport|парол|пин-?код|номер карты|cvv|смс|код из|паспорт|секрет|реквизит|құпия сөз|шот нөмір)",
    re.IGNORECASE,
)


def validate_suggestions(sugg, allowed_topics: set[str], avoid: list[str]):
    """Keep only suggestions grounded in provided topics, outside the avoid list, and non-solicitous."""
    avoid_n = [_squash(a) for a in avoid if a.strip()]
    allowed = {_squash(t) for t in allowed_topics}
    kept, dropped = [], []
    for s in sugg.suggestions:
        txt = _squash(s.text)
        if _squash(s.topic) not in allowed:
            dropped.append((s.text, "topic not among provided topics"))
        elif any(a and (a in txt or a in _squash(s.topic)) for a in avoid_n):
            dropped.append((s.text, "touches an avoided topic"))
        elif SOLICITATION.search(s.text) or SENSITIVE.search(s.text):
            dropped.append((s.text, "asks for or references sensitive information"))
        else:
            kept.append(s)
    return kept, dropped
