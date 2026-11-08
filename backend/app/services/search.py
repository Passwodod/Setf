import re
import uuid
from dataclasses import dataclass

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.concepts import match_concepts, search_terms
from app.ai.language import tokens
from app.models import Message


@dataclass
class MessageHit:
    message_id: uuid.UUID
    author_person_id: uuid.UUID | None
    group_id: uuid.UUID
    text: str
    concepts: list[str]
    reason: str


def _lexeme(tok: str) -> str:
    clean = re.sub(r"[^\w]", "", tok, flags=re.UNICODE)
    return f"{clean}:*" if len(clean) > 3 else clean


def build_tsquery(query: str) -> str | None:
    """Cross-language OR query: all aliases of recognised concepts, else the query's own words."""
    parts: list[str] = []
    expanded = search_terms(query)
    phrases = [p for aliases in expanded.values() for p in aliases] or [" ".join(tokens(query))]
    for phrase in phrases:
        lex = [_lexeme(t) for t in tokens(phrase)]
        lex = [x for x in lex if x]
        if lex:
            parts.append("(" + " <-> ".join(lex) + ")")
    return " | ".join(dict.fromkeys(parts)) or None


async def search_messages(session: AsyncSession, owner_id: uuid.UUID, query: str, *, limit: int = 25) -> list[MessageHit]:
    tsq = build_tsquery(query)
    if tsq is None:
        return []
    q = (
        select(Message)
        .where(Message.owner_id == owner_id, Message.text_tsv.op("@@")(func.to_tsquery("simple", tsq)))
        .order_by(Message.sent_at.desc())
        .limit(limit)
    )
    wanted = {m.concept for m in match_concepts(query)}
    hits = []
    for m in (await session.scalars(q)).all():
        found = [c for c in match_concepts(m.text) if not wanted or c.concept in wanted]
        concepts = sorted({c.concept for c in found})
        reason = (f"matches concept '{concepts[0]}' via '{found[0].matched_text}' ({found[0].stance})"
                  if found else "keyword match")
        hits.append(MessageHit(m.id, m.author_person_id, m.group_id, m.text, concepts, reason))
    return hits
