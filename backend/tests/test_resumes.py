"""Resume ingestion / parsing / versioning 的 API + pipeline 測試（FR-7~12）。

LLM 全程以假 provider 替換（override ``get_llm_provider`` 依賴），不連網、不花 token——
所以這些測試是 deterministic 的，CI 也能跑。
"""

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.ai.llm.base import (
    EmbeddingResult,
    LLMProvider,
    StructuredOutputError,
    StructuredResult,
    TokenUsage,
)
from app.ai.parsers.resume_schema import (
    BasicInfo,
    ExperienceItem,
    ResumeParsed,
)
from app.api.resumes import get_llm_provider
from app.main import app

_EMBEDDING_DIM = 768


def _basis(i: int) -> list[float]:
    """第 i 維為 1、其餘為 0 的單位向量（正交基底，同 test_jobs.py）。"""
    vec = [0.0] * _EMBEDDING_DIM
    vec[i] = 1.0
    return vec


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
    """可設定回傳值 / 解析錯誤 / embedding 錯誤的假 provider。"""

    def __init__(
        self,
        *,
        parsed: ResumeParsed | None = None,
        error: Exception | None = None,
        embed_error: Exception | None = None,
    ):
        self._parsed = parsed if parsed is not None else _sample_parsed()
        self._error = error
        self._embed_error = embed_error
        self.model = "fake-model"
        self.embedding_model = "fake-embed"

    def generate(self, prompt, *, system=None, temperature=0.7):  # pragma: no cover
        raise NotImplementedError

    def generate_structured(self, prompt, schema, *, system=None):
        if self._error is not None:
            raise self._error
        return StructuredResult(
            data=self._parsed,
            model=self.model,
            usage=TokenUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30),
        )

    def embed(self, texts, *, task_type="RETRIEVAL_DOCUMENT"):
        if self._embed_error is not None:
            raise self._embed_error
        return EmbeddingResult(
            vectors=[_basis(i) for i in range(len(texts))],
            model="fake-embed",
        )


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


def test_upload_generates_resume_embeddings(client, auth, use_provider, db_session):
    """上傳成功 → 為 v1 產生 summary / skills / experience 三種向量（Phase 5 前置，FR-20）。"""
    use_provider(_FakeProvider())
    headers = auth()["headers"]

    resp = client.post(
        "/resumes/upload",
        data={"text_content": "Jane Smith resume text long enough."},
        headers=headers,
    )
    assert resp.status_code == 201

    from app.db.models import ResumeEmbedding

    rows = db_session.scalars(select(ResumeEmbedding)).all()
    assert {r.kind for r in rows} == {"summary", "skills", "experience"}
    assert all(r.model == "fake-embed" for r in rows)


def test_upload_embed_failure_still_saves_resume(client, auth, use_provider, db_session):
    """Embedding 失敗 → 履歷照存 201、無 embedding rows（NFR-4；match 時 lazy backfill）。"""
    from app.ai.llm.base import LLMError

    use_provider(_FakeProvider(embed_error=LLMError("embed down")))
    headers = auth()["headers"]

    resp = client.post(
        "/resumes/upload",
        data={"text_content": "Jane Smith resume text long enough."},
        headers=headers,
    )
    assert resp.status_code == 201
    assert resp.json()["parse_status"] == "parsed"

    from app.db.models import ResumeEmbedding

    assert db_session.scalars(select(ResumeEmbedding)).all() == []


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


# --- 記帳 metadata（FR-58/59，service → LLMCallLog 接線）-----------------------


def _latest_log(db_session):
    from app.db.models import LLMCallLog

    return db_session.scalar(select(LLMCallLog).order_by(LLMCallLog.created_at.desc()).limit(1))


def test_parse_success_records_result_metadata(db_session):
    """成功路徑：model / usage / cost / attempts / repair / fallback 全取自 StructuredResult。"""
    from app.services.resume_service import parse_resume_text

    class _MetaProvider(_FakeProvider):
        def generate_structured(self, prompt, schema, *, system=None):
            return StructuredResult(
                data=self._parsed,
                model="gemini-2.5-pro",  # 模擬 fallback 成功：實際模型 ≠ provider.model
                usage=TokenUsage(prompt_tokens=30, completion_tokens=12, total_tokens=42),
                cost_estimate=Decimal("0.001234"),
                attempts=3,
                repair_used=True,
                fallback_used=True,
            )

    parsed, error = parse_resume_text(
        db_session, raw_text="some resume text", provider=_MetaProvider()
    )
    assert error is None and parsed is not None

    log = _latest_log(db_session)
    assert log.model == "gemini-2.5-pro"
    assert log.attempts == 3
    assert log.repair_used is True
    assert log.fallback_used is True
    assert log.tokens_in == 30
    assert log.cost_estimate == Decimal("0.001234")  # provider 分價加總的值原樣入帳
    assert log.status == "success"


def test_parse_failure_records_exception_metadata(db_session):
    """失敗路徑（最容易寫錯）：metadata 從例外物件取出照樣入帳。"""
    from app.services.resume_service import parse_resume_text

    err = StructuredOutputError(
        "both models failed",
        model="gemini-2.5-pro",
        attempts=4,
        fallback_used=True,
        usage=TokenUsage(prompt_tokens=20, completion_tokens=8, total_tokens=28),
    )
    parsed, error = parse_resume_text(
        db_session, raw_text="some resume text", provider=_FakeProvider(error=err)
    )
    assert parsed is None and error

    log = _latest_log(db_session)
    assert log.status == "error"
    assert log.model == "gemini-2.5-pro"
    assert log.attempts == 4
    assert log.fallback_used is True
    assert log.tokens_in == 20  # 失敗嘗試花掉的 token 也要入帳
