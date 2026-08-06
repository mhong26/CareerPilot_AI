"""Rate limiting（Phase 9）：429 行為與統一錯誤格式。

conftest 的 ``_disable_rate_limits`` autouse fixture 預設關閉限流，
此處針對性重新啟用，並在 teardown ``limiter.reset()`` 清空計數器，
避免 in-memory storage 狀態外漏到其他測試。
"""

import pytest

from app.api.deps import get_llm_provider
from app.core.ratelimit import limiter
from app.main import app


@pytest.fixture
def rate_limits_on():
    limiter.enabled = True
    yield
    limiter.enabled = False
    limiter.reset()


@pytest.fixture
def dummy_provider():
    """讓 get_llm_provider 依賴解析成功（不真的建 Gemini client）——
    限流測試只看 429 層，400 會在 provider 被使用前就回。"""
    app.dependency_overrides[get_llm_provider] = lambda: object()
    yield
    app.dependency_overrides.pop(get_llm_provider, None)


def test_login_rate_limited_after_10_requests(client, rate_limits_on):
    payload = {"email": "nobody@example.com", "password": "wrong-password"}
    for i in range(10):
        resp = client.post("/auth/login", json=payload)
        assert resp.status_code == 401, f"request {i + 1} unexpectedly limited"

    resp = client.post("/auth/login", json=payload)
    assert resp.status_code == 429
    assert "Too many requests" in resp.json()["detail"]


def test_rate_limit_key_separates_users(client, rate_limits_on, dummy_provider, auth):
    """已登入請求以 Authorization header 為 key：A 打爆額度不影響 B。"""
    user_a = auth(email="a@example.com")
    user_b = auth(email="b@example.com")

    # upload 限額 10/minute：A 用光（400：故意不帶 payload，只看限流層）
    for _ in range(10):
        resp = client.post("/resumes/upload", headers=user_a["headers"])
        assert resp.status_code == 400
    resp = client.post("/resumes/upload", headers=user_a["headers"])
    assert resp.status_code == 429

    # B 不受影響
    resp = client.post("/resumes/upload", headers=user_b["headers"])
    assert resp.status_code == 400
