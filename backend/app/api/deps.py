"""Shared FastAPI dependencies — resolve the current user from a bearer token.

Business routers added in later phases protect themselves with
``dependencies=[Depends(get_current_user)]``.
"""

import uuid

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy.orm import Session

from app.core.security import ACCESS_TOKEN_TYPE, decode_token
from app.db.models import User
from app.db.session import get_db

bearer_scheme = HTTPBearer(auto_error=False)


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None:
        raise _unauthorized()
    try:
        payload = decode_token(credentials.credentials)
    except JWTError as exc:
        raise _unauthorized() from exc
    if payload.get("type") != ACCESS_TOKEN_TYPE:
        raise _unauthorized()
    sub = payload.get("sub")
    if sub is None:
        raise _unauthorized()
    try:
        user_id = uuid.UUID(sub)
    except (ValueError, TypeError) as exc:
        raise _unauthorized() from exc
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise _unauthorized()
    return user
