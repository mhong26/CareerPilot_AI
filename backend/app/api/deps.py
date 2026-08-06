"""Shared FastAPI dependencies — current user 解析與 AI provider 注入點。

Business routers added in later phases protect themselves with
``dependencies=[Depends(get_current_user)]``.

AI provider 依賴（``get_llm_provider`` / ``get_reranker`` / ``get_planner_model``）
集中於此：建構失敗（API key 未設、模型載入失敗）轉 503 而非裸 500。
各 router re-export 同一函式物件，測試以任一 import 路徑做
``app.dependency_overrides`` 都指向同一個 key。
"""

import uuid

import structlog
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from langchain_core.language_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI
from sqlalchemy.orm import Session

from app.ai.llm.base import LLMProvider
from app.ai.llm.gemini import build_gemini_provider
from app.ai.rag.rerank import CrossEncoderReranker, Reranker
from app.core.config import settings
from app.core.security import ACCESS_TOKEN_TYPE, decode_token
from app.db.models import User
from app.db.session import get_db

logger = structlog.get_logger(__name__)

bearer_scheme = HTTPBearer(auto_error=False)


def _ai_unavailable(exc: Exception) -> HTTPException:
    logger.error("ai_service_unavailable", error=str(exc))
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="AI service is not configured or unavailable. Please try again later.",
    )


def get_llm_provider() -> LLMProvider:
    """Provider 注入點——測試可用 ``app.dependency_overrides`` 換成假 provider。"""
    try:
        return build_gemini_provider()
    except Exception as exc:
        raise _ai_unavailable(exc) from exc


def get_reranker() -> Reranker:
    """Reranker 注入點——測試換假 reranker，CI 因此永不下載 cross-encoder 模型。"""
    try:
        return CrossEncoderReranker()
    except Exception as exc:
        raise _ai_unavailable(exc) from exc


def get_planner_model() -> BaseChatModel:
    """Planner 注入點——真跑用 Gemini function calling，測試換 ScriptedPlanner。

    temperature=0：planner 做的是工具選擇決策，要穩定不要創意（創意留給
    generate 工具內的 wrapper 呼叫）。
    """
    if not settings.gemini_api_key:
        raise _ai_unavailable(ValueError("GEMINI_API_KEY is not set"))
    try:
        return ChatGoogleGenerativeAI(
            model=settings.gemini_model,
            google_api_key=settings.gemini_api_key,
            temperature=0,
        )
    except Exception as exc:
        raise _ai_unavailable(exc) from exc


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
