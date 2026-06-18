"""Password hashing (bcrypt) and JWT encode/decode for access + refresh tokens.

A ``type`` claim distinguishes access from refresh tokens so one can never be
used in place of the other. Refresh tokens additionally carry a random ``jti``
that is tracked in the ``refresh_tokens`` table for revocation.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
from jose import jwt

from app.core.config import settings

ACCESS_TOKEN_TYPE = "access"
REFRESH_TOKEN_TYPE = "refresh"

# bcrypt only considers the first 72 bytes; truncate explicitly so bcrypt 4.x
# doesn't raise on longer (e.g. multibyte) input. Schemas also cap length.
_BCRYPT_MAX_BYTES = 72


def _to_bcrypt_bytes(plain_password: str) -> bytes:
    return plain_password.encode("utf-8")[:_BCRYPT_MAX_BYTES]


def hash_password(plain_password: str) -> str:
    return bcrypt.hashpw(_to_bcrypt_bytes(plain_password), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(_to_bcrypt_bytes(plain_password), hashed_password.encode("utf-8"))


def _encode(subject: str, token_type: str, expires_at: datetime, jti: str | None = None) -> str:
    payload: dict[str, Any] = {
        "sub": subject,
        "type": token_type,
        "iat": int(datetime.now(UTC).timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    if jti is not None:
        payload["jti"] = jti
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_access_token(subject: str) -> str:
    expires_at = datetime.now(UTC) + timedelta(minutes=settings.jwt_access_token_expire_minutes)
    return _encode(subject, ACCESS_TOKEN_TYPE, expires_at)


def create_refresh_token(subject: str) -> tuple[str, str, datetime]:
    """Return ``(token, jti, expires_at)`` — the caller persists jti + expiry."""
    jti = uuid.uuid4().hex
    expires_at = datetime.now(UTC) + timedelta(days=settings.jwt_refresh_token_expire_days)
    token = _encode(subject, REFRESH_TOKEN_TYPE, expires_at, jti=jti)
    return token, jti, expires_at


def decode_token(token: str) -> dict[str, Any]:
    """Decode + verify signature/expiry. Raises ``jose.JWTError`` on failure."""
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
