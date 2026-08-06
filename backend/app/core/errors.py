"""全域例外 handlers：把會以裸 500 逃逸的基礎設施類例外轉成統一 ``{"detail": ...}``。

域內例外（``JobNotFoundError`` 等）仍由各 router 就地轉 ``HTTPException``；
LLM 失敗的服務層降級（``parse_error`` 等欄位 + 2xx）也維持原路徑。這裡只接：
DB 斷線、資料完整性衝突、JSONB 資料漂移（``model_validate`` 失敗）與未知例外。

注意：Starlette 對 ``Exception`` 的 catch-all handler 送出回應後仍會 re-raise
（讓 server 層記錄），因此測試中 ``TestClient`` 需以
``raise_server_exceptions=False`` 才能觀察到 500 回應本體。
"""

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from slowapi.errors import RateLimitExceeded
from sqlalchemy.exc import IntegrityError, OperationalError

logger = structlog.get_logger(__name__)


async def _rate_limit_handler(request: Request, exc: Exception) -> JSONResponse:
    # slowapi 預設 handler 的 body 格式與本專案 {"detail": ...} 慣例不同，自訂統一。
    logger.warning("rate_limit_exceeded", path=request.url.path)
    return JSONResponse(
        status_code=429,
        content={"detail": "Too many requests. Please slow down and try again shortly."},
    )


async def _operational_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error("database_unavailable", path=request.url.path, error=str(exc))
    return JSONResponse(
        status_code=503,
        content={"detail": "Database unavailable. Please try again shortly."},
    )


async def _integrity_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error("integrity_error", path=request.url.path, error=str(exc))
    return JSONResponse(
        status_code=409,
        content={"detail": "Conflicting data state. Please retry the request."},
    )


async def _stored_data_validation_handler(request: Request, exc: Exception) -> JSONResponse:
    # pydantic.ValidationError 逃逸到這層代表 DB 內 JSONB 與 schema 漂移；
    # request 端的驗證失敗走 FastAPI 自己的 RequestValidationError（422），不經此處。
    logger.error("stored_data_invalid", path=request.url.path, error=str(exc))
    return JSONResponse(
        status_code=500,
        content={"detail": "Stored data failed validation. Please contact support."},
    )


async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled_exception", path=request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error."})


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(OperationalError, _operational_error_handler)
    app.add_exception_handler(IntegrityError, _integrity_error_handler)
    app.add_exception_handler(ValidationError, _stored_data_validation_handler)
    app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)
    app.add_exception_handler(Exception, _unhandled_exception_handler)
