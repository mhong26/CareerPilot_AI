"""Resume ingestion / parsing / versioning 的 API + pipeline 測試（FR-7~12）。

LLM 全程以假 provider 替換（override ``get_llm_provider`` 依賴），不連網、不花 token——
所以這些測試是 deterministic 的，CI 也能跑。
"""

import pytest

from app.ai.llm.base import LLMProvider, StructuredOutputError, TokenUsage
from app.ai.parsers.resume_schema import (
    BasicInfo,
    ExperienceItem,
    ResumeParsed,
)
from app.api.resumes import get_llm_provider
from app.main import app

# --- 假 provider --------------------------------------------------------------


def _sample_parsed(skills: list[str] | None = None) -> ResumeParsed:
    return ResumeParsed(
        basic_info=BasicInfo(
            name="Jane Smith", email="jane@example.com", phone="", location="", links=[]
        ),
        summary="Backend engineer.",
        skills=skills if skills is not None else ["Python", "FastAPI"],
        experience=[
            ExperienceItem(
                company="Acme",
                title="Engineer",
                start_date="2020",
                end_date="2023",
                bullets=["Built APIs"],
            )
        ],
        projects=[],
        education=[],
        certifications=[],
    )


class _FakeProvider(LLMProvider):
    """可設定回傳值或丟錯的假 provider。"""

    def __init__(self, *, parsed: ResumeParsed | None = None, error: Exception | None = None):
        self._parsed = parsed if parsed is not None else _sample_parsed()
        self._error = error
        self.model = "fake-model"

    def generate(self, prompt, *, system=None, temperature=0.7):  # pragma: no cover
        raise NotImplementedError

    def generate_structured(self, prompt, schema, *, system=None):
        if self._error is not None:
            raise self._error
        return self._parsed, TokenUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30)

    def embed(self, texts, *, task_type="RETRIEVAL_DOCUMENT"):  # pragma: no cover
        raise NotImplementedError


@pytest.fixture
def use_provider(client):
    """把 get_llm_provider 換成指定的假 provider（client teardown 會自動清掉 override）。"""

    def _use(provider: LLMProvider) -> None:
        app.dependency_overrides[get_llm_provider] = lambda: provider

    return _use


# --- 測試 ---------------------------------------------------------------------


def test_upload_text_happy_path(client, auth, use_provider):
    """貼上文字 → 解析成功 → 建立 v1(original)。"""
    use_provider(_FakeProvider())
    headers = auth()["headers"]

    resp = client.post(
        "/resumes/upload",
        data={"text_content": "Jane Smith resume text long enough to pass."},
        headers=headers,
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["parse_status"] == "parsed"
    assert body["source_type"] == "text"
    assert body["current_version_number"] == 1
    assert body["parsed_data"]["skills"] == ["Python", "FastAPI"]


def test_upload_parse_failure_still_creates_resume(client, auth, use_provider):
    """LLM 解析失敗 → 仍回 201、parse_status=failed、有 parse_error、無 version（FR-10 容錯）。"""
    use_provider(_FakeProvider(error=StructuredOutputError("bad schema")))
    headers = auth()["headers"]

    resp = client.post(
        "/resumes/upload",
        data={"text_content": "Some resume text that is long enough."},
        headers=headers,
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["parse_status"] == "failed"
    assert body["parse_error"]
    assert body["current_version_number"] is None
    assert body["parsed_data"] is None


def test_upload_requires_exactly_one_input(client, auth, use_provider):
    """檔案與文字都不給 → 400。"""
    use_provider(_FakeProvider())
    headers = auth()["headers"]

    resp = client.post("/resumes/upload", data={}, headers=headers)
    assert resp.status_code == 400


def test_upload_unsupported_file_type(client, auth, use_provider):
    """不支援的副檔名 → 415（抽取階段擋下，不會呼叫 LLM）。"""
    use_provider(_FakeProvider())
    headers = auth()["headers"]

    resp = client.post(
        "/resumes/upload",
        files={"file": ("photo.png", b"\x89PNG fake bytes", "image/png")},
        headers=headers,
    )
    assert resp.status_code == 415


def test_patch_creates_new_version(client, auth, use_provider):
    """編輯 → 存成新版本；versions 列出 v1(original) + v2(edit)。"""
    use_provider(_FakeProvider())
    headers = auth()["headers"]

    rid = client.post(
        "/resumes/upload",
        data={"text_content": "Jane Smith resume text long enough."},
        headers=headers,
    ).json()["id"]

    edited = _sample_parsed(skills=["Python", "FastAPI", "Docker"]).model_dump()
    patch = client.patch(f"/resumes/{rid}", json={"parsed_data": edited}, headers=headers)
    assert patch.status_code == 200
    assert patch.json()["current_version_number"] == 2
    assert "Docker" in patch.json()["parsed_data"]["skills"]

    versions = client.get(f"/resumes/{rid}/versions", headers=headers).json()
    assert [(v["version_number"], v["label"]) for v in versions] == [
        (1, "original"),
        (2, "edit"),
    ]


def test_get_current_returns_latest(client, auth, use_provider):
    """連傳兩份 → /current 回最新一份。"""
    use_provider(_FakeProvider())
    headers = auth()["headers"]

    client.post(
        "/resumes/upload",
        data={"text_content": "First resume text long enough."},
        headers=headers,
    )
    second_id = client.post(
        "/resumes/upload",
        data={"text_content": "Second resume text long enough."},
        headers=headers,
    ).json()["id"]

    current = client.get("/resumes/current", headers=headers)
    assert current.status_code == 200
    assert current.json()["id"] == second_id


def test_user_isolation(client, auth, use_provider):
    """User B 取不到 User A 的履歷 → 404（FR-4 / NFR-8）。"""
    use_provider(_FakeProvider())
    a_headers = auth(email="alice@example.com")["headers"]
    b_headers = auth(email="bob@example.com")["headers"]

    rid = client.post(
        "/resumes/upload",
        data={"text_content": "Alice resume text long enough."},
        headers=a_headers,
    ).json()["id"]

    assert client.get(f"/resumes/{rid}", headers=b_headers).status_code == 404
    assert client.get(f"/resumes/{rid}/versions", headers=b_headers).status_code == 404


def test_requires_auth(client):
    """未登入存取受保護路由 → 401。"""
    assert client.get("/resumes/current").status_code == 401
