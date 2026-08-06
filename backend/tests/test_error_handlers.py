"""全域錯誤處理（Phase 9 M1/M2）：DB down → 503、provider 未配置 → 503、
未知例外 → 500 JSON（非裸 traceback）、/health degraded。"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.api.deps import get_llm_provider
from app.core.config import settings
from app.db.session import get_db
from app.main import app
from tests.conftest import override_get_db


def _db_down():
    raise OperationalError("SELECT 1", None, Exception("db down"))
    yield  # pragma: no cover


def test_db_down_returns_readable_503(client, auth):
    headers = auth()["headers"]
    app.dependency_overrides[get_db] = _db_down

    resp = client.get("/auth/me", headers=headers)
    assert resp.status_code == 503
    assert "Database unavailable" in resp.json()["detail"]


def test_health_degraded_when_db_down(client):
    class _BrokenSession:
        def execute(self, *args, **kwargs):
            raise OperationalError("SELECT 1", None, Exception("db down"))

    def _broken_db():
        yield _BrokenSession()

    app.dependency_overrides[get_db] = _broken_db
    resp = client.get("/health")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["db"].startswith("error")


def test_missing_api_key_returns_503(client, auth, monkeypatch):
    """GEMINI_API_KEY 未設 → provider 依賴回 503，而非裸 500。"""
    monkeypatch.setattr(settings, "gemini_api_key", "")
    headers = auth()["headers"]

    resp = client.post(
        "/jobs",
        json={"raw_text": "A long enough job description text."},
        headers=headers,
    )
    assert resp.status_code == 503
    assert "AI service" in resp.json()["detail"]


@pytest.fixture
def tolerant_client():
    """raise_server_exceptions=False：觀察 catch-all handler 的 500 回應本體
    （Starlette 對 Exception handler 送出回應後仍會 re-raise 給 server 層）。"""
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.clear()


def test_unhandled_exception_returns_json_500(tolerant_client):
    def _boom():
        raise RuntimeError("totally unexpected")

    app.dependency_overrides[get_llm_provider] = _boom
    tolerant_client.post(
        "/auth/register",
        json={
            "email": "boom@example.com",
            "password": "Sup3rSecret!",
            "full_name": "Boom",
        },
    )
    resp = tolerant_client.post(
        "/auth/login", json={"email": "boom@example.com", "password": "Sup3rSecret!"}
    )
    headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}

    resp = tolerant_client.post(
        "/jobs",
        json={"raw_text": "A long enough job description text."},
        headers=headers,
    )
    assert resp.status_code == 500
    assert resp.json() == {"detail": "Internal server error."}
