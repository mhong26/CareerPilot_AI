"""Authentication business logic — registration, login, token issue/rotate/revoke.

Routers stay thin and translate the domain errors below into HTTP responses.
"""

import uuid
from typing import Any

from jose import JWTError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import (
    REFRESH_TOKEN_TYPE,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.db.models import RefreshToken, User
from app.schemas.auth import Token, UserCreate


class EmailAlreadyExistsError(Exception):
    """Raised when registering an email that already exists."""


class InvalidTokenError(Exception):
    """Raised when a refresh token is malformed, expired, or revoked."""


def get_user_by_email(db: Session, email: str) -> User | None:
    return db.scalar(select(User).where(User.email == email))


def register_user(db: Session, data: UserCreate) -> User:
    if get_user_by_email(db, data.email) is not None:
        raise EmailAlreadyExistsError(data.email)
    user = User(
        email=data.email,
        hashed_password=hash_password(data.password),
        full_name=data.full_name,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def authenticate_user(db: Session, email: str, password: str) -> User | None:
    """Return the user on success, ``None`` on bad email OR bad password.

    The caller maps both misses to the same 401 to avoid user enumeration.
    """
    user = get_user_by_email(db, email)
    if user is None or not verify_password(password, user.hashed_password):
        return None
    return user


def issue_token_pair(db: Session, user: User) -> Token:
    access_token = create_access_token(str(user.id))
    refresh_token, jti, expires_at = create_refresh_token(str(user.id))
    db.add(RefreshToken(user_id=user.id, jti=jti, expires_at=expires_at))
    db.commit()
    return Token(access_token=access_token, refresh_token=refresh_token)


def _decode_refresh(token: str) -> dict[str, Any]:
    try:
        payload = decode_token(token)
    except JWTError as exc:
        raise InvalidTokenError("invalid refresh token") from exc
    if payload.get("type") != REFRESH_TOKEN_TYPE:
        raise InvalidTokenError("not a refresh token")
    return payload


def rotate_refresh(db: Session, token: str) -> Token:
    """Validate a refresh token, revoke it, and issue a fresh access+refresh pair."""
    payload = _decode_refresh(token)
    record = db.scalar(select(RefreshToken).where(RefreshToken.jti == payload.get("jti")))
    if record is None or record.revoked:
        raise InvalidTokenError("refresh token revoked or unknown")
    user = db.get(User, uuid.UUID(payload["sub"]))
    if user is None or not user.is_active:
        raise InvalidTokenError("user not found or inactive")
    record.revoked = True
    # issue_token_pair commits, persisting the revoke and the new token together.
    return issue_token_pair(db, user)


def revoke_refresh(db: Session, token: str) -> None:
    """Mark a refresh token revoked. Idempotent — unknown/expired tokens no-op."""
    try:
        payload = _decode_refresh(token)
    except InvalidTokenError:
        return
    record = db.scalar(select(RefreshToken).where(RefreshToken.jti == payload.get("jti")))
    if record is not None and not record.revoked:
        record.revoked = True
        db.commit()
