"""Signed, tamper-proof deep-link tokens that tie a consent prompt to one person row."""
import base64
import hashlib
import hmac
import uuid

from app.core.config import get_settings


def _key() -> bytes:
    return hashlib.sha256(b"consent-invite:" + get_settings().jwt_secret.get_secret_value().encode()).digest()


def make_token(person_id: uuid.UUID) -> str:
    tag = hmac.new(_key(), person_id.bytes, hashlib.sha256).digest()[:16]
    return base64.urlsafe_b64encode(person_id.bytes + tag).decode().rstrip("=")


def parse_token(token: str) -> uuid.UUID | None:
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    except Exception:
        return None
    if len(raw) != 32:
        return None
    pid, tag = raw[:16], raw[16:]
    if not hmac.compare_digest(tag, hmac.new(_key(), pid, hashlib.sha256).digest()[:16]):
        return None
    return uuid.UUID(bytes=pid)
