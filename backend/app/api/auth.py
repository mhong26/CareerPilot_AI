"""Authentication routes: register, login, refresh, logout, me."""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.ratelimit import limiter
from app.db.models import User
from app.db.session import get_db
from app.schemas.auth import Token, TokenRefresh, UserCreate, UserLogin, UserResponse
from app.services import auth_service
from app.services.auth_service import EmailAlreadyExistsError, InvalidTokenError

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit(settings.rate_limit_auth)
def register(request: Request, data: UserCreate, db: Session = Depends(get_db)) -> User:
    try:
        return auth_service.register_user(db, data)
    except EmailAlreadyExistsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        ) from exc


@router.post("/login", response_model=Token)
@limiter.limit(settings.rate_limit_auth)
def login(request: Request, data: UserLogin, db: Session = Depends(get_db)) -> Token:
    user = auth_service.authenticate_user(db, data.email, data.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return auth_service.issue_token_pair(db, user)


@router.post("/refresh", response_model=Token)
@limiter.limit("30/minute")
def refresh(request: Request, data: TokenRefresh, db: Session = Depends(get_db)) -> Token:
    try:
        return auth_service.rotate_refresh(db, data.refresh_token)
    except InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(data: TokenRefresh, db: Session = Depends(get_db)) -> None:
    auth_service.revoke_refresh(db, data.refresh_token)


@router.get("/me", response_model=UserResponse)
def me(current_user: User = Depends(get_current_user)) -> User:
    return current_user
