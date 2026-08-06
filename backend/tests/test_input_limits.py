"""輸入限制（Phase 9 M3）：貼文長度上限、檔案大小上限、API 層 415。"""

import io

import pytest

from app.api.deps import get_llm_provider
from app.core.config import settings
from app.main import app
from tests.fakes import FakeProvider


@pytest.fixture
def provider(client):
    app.dependency_overrides[get_llm_provider] = FakeProvider
    # client teardown 清 overrides


def test_resume_text_over_limit_returns_413(client, auth, provider):
    headers = auth()["headers"]
    resp = client.post(
        "/resumes/upload",
        data={"text_content": "x" * (settings.max_text_input_chars + 1)},
        headers=headers,
    )
    assert resp.status_code == 413
    assert "character limit" in resp.json()["detail"]


def test_job_text_over_limit_returns_422(client, auth, provider):
    headers = auth()["headers"]
    resp = client.post("/jobs", json={"raw_text": "x" * 50_001}, headers=headers)
    assert resp.status_code == 422


def test_oversize_file_returns_413(client, auth, provider, monkeypatch):
    monkeypatch.setattr(settings, "max_upload_size_mb", 1)
    payload = b"%PDF-" + b"0" * (1024 * 1024 + 16)
    headers = auth()["headers"]
    resp = client.post(
        "/resumes/upload",
        files={"file": ("big.pdf", io.BytesIO(payload), "application/pdf")},
        headers=headers,
    )
    assert resp.status_code == 413
    assert "MB limit" in resp.json()["detail"]


def test_renamed_zip_returns_415(client, auth, provider):
    """API 層驗證 magic-byte 白名單：ZIP 改名 .pdf → 415。"""
    headers = auth()["headers"]
    resp = client.post(
        "/resumes/upload",
        files={"file": ("fake.pdf", io.BytesIO(b"PK\x03\x04zipzip"), "application/pdf")},
        headers=headers,
    )
    assert resp.status_code == 415
