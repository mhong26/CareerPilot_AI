"""End-to-end 整合測試（Phase 9）：register → upload resume → add job →
match → application kit，全程走 HTTP 層（httpx.AsyncClient + ASGITransport）。

LLM / reranker / planner 依賴以 M1 集中後的 ``app.api.deps`` 物件一次 override
（各 router re-export 同一函式物件）。planner 以 factory 注入——每請求拿到
cursor 歸零的新 ScriptedPlanner。
"""

import httpx
import pytest
from httpx import ASGITransport

from app.api.deps import get_llm_provider, get_planner_model, get_reranker
from app.db.session import get_db
from app.main import app
from tests.agent_fakes import ScriptedPlanner, tool_call
from tests.conftest import DEFAULT_PASSWORD, override_get_db
from tests.fakes import FakeProvider, NoopReranker


def _happy_script():
    return [
        tool_call("fetch_resume", "c1"),
        tool_call("compute_match", "c2"),
        tool_call("generate_tailored_resume", "c3"),
        tool_call("save_artifact", "c4", {"kind": "tailored_resume"}),
        tool_call("generate_cover_letter", "c5"),
        tool_call("save_artifact", "c6", {"kind": "cover_letter"}),
        tool_call("generate_interview_qs", "c7"),
        tool_call("save_artifact", "c8", {"kind": "interview_prep"}),
    ]


@pytest.fixture
async def e2e_client():
    provider = FakeProvider()
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_llm_provider] = lambda: provider
    app.dependency_overrides[get_reranker] = NoopReranker
    app.dependency_overrides[get_planner_model] = lambda: ScriptedPlanner(script=_happy_script())
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


async def test_full_user_journey(e2e_client):
    # 1. Register
    resp = await e2e_client.post(
        "/auth/register",
        json={
            "email": "journey@example.com",
            "password": DEFAULT_PASSWORD,
            "full_name": "Journey User",
        },
    )
    assert resp.status_code == 201

    # 2. Login
    resp = await e2e_client.post(
        "/auth/login",
        json={"email": "journey@example.com", "password": DEFAULT_PASSWORD},
    )
    assert resp.status_code == 200
    tokens = resp.json()
    assert tokens["access_token"] and tokens["refresh_token"]
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    # 3. Upload resume (paste-text path) → structured parse succeeded
    resp = await e2e_client.post(
        "/resumes/upload",
        data={"text_content": "Jane Smith, backend engineer resume text long enough."},
        headers=headers,
    )
    assert resp.status_code == 201
    resume = resp.json()
    assert resume["parse_status"] == "parsed"
    assert resume["parsed_data"] is not None
    assert resume["parsed_data"]["skills"] == ["Python", "Golang"]
    resume_id = resume["id"]

    # 4. Add a job → parsed and vector-indexed
    resp = await e2e_client.post(
        "/jobs",
        json={"raw_text": "Backend Engineer role: build APIs with Python and Go."},
        headers=headers,
    )
    assert resp.status_code == 201
    job = resp.json()
    assert job["parse_status"] == "parsed"
    assert job["index_status"] == "indexed"
    assert job["chunk_count"] > 0
    job_id = job["id"]

    # 5. Run matching → scored + explained result
    resp = await e2e_client.post(
        "/matches/run",
        json={"resume_id": resume_id, "job_ids": [job_id]},
        headers=headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["skipped"] == []
    assert len(body["results"]) == 1
    item = body["results"][0]
    assert 0.0 <= item["match_score"] <= 1.0
    assert item["explanation"] is not None
    assert item["explanation"]["why_matched"]

    # 6. Generate the application kit → all three artifacts saved in one run
    resp = await e2e_client.post(
        f"/jobs/{job_id}/generate-application-kit", json={}, headers=headers
    )
    assert resp.status_code == 200
    kit = resp.json()
    assert kit["missing"] == []
    assert kit["errors"] == []
    for kind in ("tailored_resume", "cover_letter", "interview_prep"):
        assert kit[kind] is not None
        assert kit[kind]["kind"] == kind

    # 7. Kit is persisted and retrievable
    resp = await e2e_client.get(
        f"/jobs/{job_id}/application-kit",
        params={"resume_id": resume_id},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["cover_letter"]["content"]["intro"] == "Dear team,"

    # 8. Health stays green and every response carries a request id
    resp = await e2e_client.get("/health")
    assert resp.status_code == 200
    assert resp.headers.get("X-Request-ID")
