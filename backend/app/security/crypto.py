from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.core.config import get_settings


class CryptoError(Exception):
    pass


def _fernet() -> MultiFernet:
    raw = get_settings().session_encryption_keys.get_secret_value()
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    if not keys:
        raise CryptoError("SIA_SESSION_ENCRYPTION_KEYS is not configured")
    try:
        return MultiFernet([Fernet(k.encode()) for k in keys])
    except ValueError as exc:
        raise CryptoError("invalid encryption key") from exc


def encrypt_secret(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise CryptoError("cannot decrypt (wrong key or corrupted data)") from exc


def rotate_secret(token: str) -> str:
    """Re-encrypt under the newest key (key rotation)."""
    return _fernet().rotate(token.encode()).decode()
