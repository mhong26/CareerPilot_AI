"""Rate limiting（slowapi）：保護昂貴的 LLM endpoints 與 auth brute force。

Key 策略：已登入請求以 Authorization header（bearer token）為 key ≈ per-user
（不解 JWT，零額外成本）；未登入（login / register）以 client IP 為 key。

``limiter.enabled`` 於 request 時檢查——測試在 conftest 以 autouse fixture
直接關閉，不需要 env / import 順序技巧。
"""

from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request

from app.core.config import settings


def rate_limit_key(request: Request) -> str:
    auth = request.headers.get("authorization")
    if auth:
        return auth
    return get_remote_address(request)


limiter = Limiter(key_func=rate_limit_key, enabled=settings.rate_limit_enabled)
