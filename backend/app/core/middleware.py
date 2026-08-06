"""Request-ID / access-log middleware（Phase 9 硬化）。

每個請求：取（或生成）``X-Request-ID`` → 綁進 structlog contextvars（此後
該請求路徑上所有 log 行自動帶 request_id）→ 記 request_started /
request_completed（status、duration_ms）→ response header 回傳 request id。

不記 Authorization header 與 request/response body（避免洩漏憑證與個資）。
"""

import time
import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

logger = structlog.get_logger("app.request")


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
        )
        start = time.perf_counter()
        logger.info("request_started")
        try:
            response = await call_next(request)
        except Exception:
            # 未被 route/handler 層接住的例外：記完整 traceback 後續丟給
            # ServerErrorMiddleware（catch-all handler 在那層回 500 JSON）
            duration_ms = round((time.perf_counter() - start) * 1000, 1)
            logger.exception("request_failed", duration_ms=duration_ms)
            raise
        else:
            duration_ms = round((time.perf_counter() - start) * 1000, 1)
            logger.info(
                "request_completed",
                status_code=response.status_code,
                duration_ms=duration_ms,
            )
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            structlog.contextvars.clear_contextvars()
