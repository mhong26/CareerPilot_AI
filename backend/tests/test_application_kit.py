"""Application kit 測試：Step 5 生成函式 + Step 6 agent 工具層。

生成函式（Step 5）：真 DB 記帳、假 provider。
工具層（Step 6）：經 API 種好 user/resume/job（同 test_skill_gaps 的假 provider
正交基底幾何——resume 三段文字 → b0/b1/b2；2-chunk job：responsibilities(b0)、
required_skills(b1)；query kind=skills(b1) → 向量序恆為 [required_skills,
responsibilities]），再直接呼叫 closure 工具斷言 ctx 讀寫與回傳摘要。

Phase 7 Step 10 會在此擴充 graph / API 整合測試（scripted planner 四情境）。
"""

import time
import uuid

import pytest
from sqlalchemy import select

from app.ai.agents.context import KitRunContext
from app.ai.agents.tools import build_kit_tools
from app.ai.llm.base import (
    EmbeddingResult,
    LLMError,
    LLMProvider,
    StructuredOutputError,
    StructuredResult,
    TokenUsage,
)
from app.ai.parsers.job_schema import JobParsed
from app.ai.parsers.kit_schema import (
    CoverLetterDraft,
    InterviewPrepSet,
    InterviewQuestion,
    TailoredResumeSuggestions,
)
from app.ai.parsers.resume_schema import BasicInfo, ExperienceItem, ProjectItem, ResumeParsed
from app.ai.prompts.kit import (
    COVER_LETTER_SYSTEM,
    INTERVIEW_QS_SYSTEM,
    TAILORED_RESUME_SYSTEM,
)
from app.api.jobs import get_llm_provider as jobs_get_llm_provider
from app.api.resumes import get_llm_provider as resumes_get_llm_provider
from app.db.models import GeneratedArtifact, Job, LLMCallLog, Resume, ResumeVersion, User
from app.main import app
from app.services.application_kit_service import (
    generate_cover_letter_payload,
    generate_interview_prep_payload,
    generate_tailored_resume_payload,
)

_EMBEDDING_DIM = 768


def _basis(i: int) -> list[float]:
    """第 i 維為 1、其餘為 0 的單位向量（正交基底）。"""
    vec = [0.0] * _EMBEDDING_DIM
    vec[i] = 1.0
    return vec


# --- 固定資料 ------------------------------------------------------------------


def _sample_resume_parsed() -> ResumeParsed:
    return ResumeParsed(
        basic_info=BasicInfo(name="Jane Smith", email="jane@example.com"),
        summary="Backend engineer.",
        skills=["Python", "Golang"],
        experience=[
            ExperienceItem(
                company="Acme",
                title="Engineer",
                start_date="Jan 2020",
                end_date="Jan 2023",
                bullets=["Built APIs"],
            )
        ],
        projects=[ProjectItem(name="Sidecar", bullets=["Wrote proxy"], tech=["Go"])],
    )


def _sample_job_parsed() -> JobParsed:
    """2-chunk job（overview 欄位全空 → 略過）：responsibilities(b0)、required_skills(b1)。"""
    return JobParsed(responsibilities=["Build APIs"], required_skills=["Python", "Go"])


def _sample_tailored() -> TailoredResumeSuggestions:
    return TailoredResumeSuggestions(overall_strategy="Lead with backend work.")


def _sample_cover() -> CoverLetterDraft:
    return CoverLetterDraft(
        intro="Dear team,", body_paragraphs=["I built APIs."], closing="Thanks."
    )


def _sample_prep() -> InterviewPrepSet:
    return InterviewPrepSet(
        questions=[InterviewQuestion(question="Why us?", category="behavioral")]
    )


class _FakeProvider(LLMProvider):
    """按 schema 分派的假 provider——服務解析、三類 kit 生成與 embed。

    match 的 SkillEquivalenceResult / MatchExplanation 落到 else 分支拋
    ``LLMError`` → match_service 既有降級路徑（exact-only、explanation_error）。
    """

    def __init__(
        self,
        *,
        tailored: TailoredResumeSuggestions | None = None,
        cover: CoverLetterDraft | None = None,
        prep: InterviewPrepSet | None = None,
        kit_error: Exception | None = None,
    ):
        self._tailored = tailored if tailored is not None else _sample_tailored()
        self._cover = cover if cover is not None else _sample_cover()
        self._prep = prep if prep is not None else _sample_prep()
        self._kit_error = kit_error
        self.model = "fake-model"
        self.embedding_model = "fake-embed"
        self.seen_schema: type | None = None
        self.seen_system: str | None = None

    def generate(self, prompt, *, system=None, temperature=0.7):  # pragma: no cover
        raise NotImplementedError

    def generate_structured(self, prompt, schema, *, system=None):
        self.seen_schema = schema
        self.seen_system = system
        data: object
        if schema is ResumeParsed:
            data = _sample_resume_parsed()
        elif schema is JobParsed:
            data = _sample_job_parsed()
        elif schema in (TailoredResumeSuggestions, CoverLetterDraft, InterviewPrepSet):
            if self._kit_error is not None:
                raise self._kit_error
            data = {
                TailoredResumeSuggestions: self._tailored,
                CoverLetterDraft: self._cover,
                InterviewPrepSet: self._prep,
            }[schema]
        else:
            # match 等其他 schema：降級路徑（服務層 catch LLMError）。
            raise LLMError("fake: unsupported schema")
        return StructuredResult(
            data=data,
            model=self.model,
            usage=TokenUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30),
        )

    def embed(self, texts, *, task_type="RETRIEVAL_DOCUMENT"):
        return EmbeddingResult(vectors=[_basis(i) for i in range(len(texts))], model="fake-embed")


class _NoopReranker:
    """遞減分數 → 保持向量序；工具測試絕不載入真 cross-encoder。"""

    def predict(self, query: str, passages: list[str]) -> list[float]:
        return [float(len(passages) - i) for i in range(len(passages))]


class _FailingReranker:
    def predict(self, query: str, passages: list[str]) -> list[float]:
        raise RuntimeError("rerank down")


# --- Step 5：生成函式 -----------------------------------------------------------


def _logs(db_session):
    return db_session.execute(select(LLMCallLog)).scalars().all()


@pytest.mark.parametrize(
    ("func", "payload_kwarg", "payload", "expected_schema", "expected_system"),
    [
        (
            generate_tailored_resume_payload,
            "tailored",
            _sample_tailored(),
            TailoredResumeSuggestions,
            TAILORED_RESUME_SYSTEM,
        ),
        (
            generate_cover_letter_payload,
            "cover",
            _sample_cover(),
            CoverLetterDraft,
            COVER_LETTER_SYSTEM,
        ),
        (
            generate_interview_prep_payload,
            "prep",
            _sample_prep(),
            InterviewPrepSet,
            INTERVIEW_QS_SYSTEM,
        ),
    ],
)
def test_generate_payload_success_logs_call(
    db_session, func, payload_kwarg, payload, expected_schema, expected_system
):
    """成功：回 payload、綁對 schema/system、LLMCallLog 記一筆 success。"""
    provider = _FakeProvider(**{payload_kwarg: payload})
    data, error = func(db_session, prompt="the prompt", provider=provider)

    assert error is None
    assert data is payload
    assert provider.seen_schema is expected_schema
    assert provider.seen_system == expected_system

    logs = _logs(db_session)
    assert len(logs) == 1
    log = logs[0]
    assert log.status == "success"
    assert log.operation == "generate_structured"
    assert log.model == "fake-model"
    assert log.tokens_in == 10
    assert log.tokens_out == 20


def test_generate_payload_failure_degrades(db_session):
    """失敗：回 (None, error) 不丟例外，LLMCallLog 記一筆 error（NFR-4、FR-65）。"""
    provider = _FakeProvider(kit_error=StructuredOutputError("cover letter gen down"))
    data, error = generate_cover_letter_payload(db_session, prompt="the prompt", provider=provider)

    assert data is None
    assert "cover letter gen down" in error

    logs = _logs(db_session)
    assert len(logs) == 1
    assert logs[0].status == "error"
    assert "cover letter gen down" in logs[0].error


# --- Step 6：agent 工具層 -------------------------------------------------------


@pytest.fixture
def kit_ctx(client, auth, db_session):
    """經 API 種好 user/resume/job，回「建好 KitRunContext」的工廠。"""

    def _make(provider=None, reranker=None) -> KitRunContext:
        provider = provider if provider is not None else _FakeProvider()
        headers = auth()["headers"]
        for key in (resumes_get_llm_provider, jobs_get_llm_provider):
            app.dependency_overrides[key] = lambda p=provider: p
        resp = client.post(
            "/resumes/upload",
            data={"text_content": "Jane Smith resume text long enough."},
            headers=headers,
        )
        assert resp.status_code == 201
        resp = client.post(
            "/jobs", json={"raw_text": "A long enough job description text."}, headers=headers
        )
        assert resp.status_code == 201
        return KitRunContext(
            db=db_session,
            user=db_session.scalar(select(User)),
            resume=db_session.scalar(select(Resume)),
            version=db_session.scalar(select(ResumeVersion)),
            job=db_session.scalar(select(Job)),
            provider=provider,
            reranker=reranker if reranker is not None else _NoopReranker(),
            run_id=uuid.uuid4(),
            deadline=time.monotonic() + 240.0,
        )

    return _make


def _tools_by_name(ctx: KitRunContext):
    return {t.name: t for t in build_kit_tools(ctx)}


def test_build_kit_tools_names(kit_ctx):
    """7 個工具、名稱與 FR-57 逐字一致、順序即 SRS 條列順序。"""
    names = [t.name for t in build_kit_tools(kit_ctx())]
    assert names == [
        "fetch_resume",
        "retrieve_job_evidence",
        "compute_match",
        "generate_tailored_resume",
        "generate_cover_letter",
        "generate_interview_qs",
        "save_artifact",
    ]


def test_fetch_resume_tool(kit_ctx):
    """parsed 存入 ctx；摘要含姓名與 top skills，不含完整 JSON。"""
    ctx = kit_ctx()
    message = _tools_by_name(ctx)["fetch_resume"].invoke({})

    assert ctx.resume_parsed is not None
    assert ctx.resume_parsed.basic_info.name == "Jane Smith"
    assert "Jane Smith" in message
    assert "Python" in message
    assert "1 role(s)" in message


def test_retrieve_job_evidence_tool(kit_ctx):
    """lazy backfill 履歷向量 → 檢索 → rerank（noop 保持向量序）→ 編號摘要。"""
    ctx = kit_ctx()
    message = _tools_by_name(ctx)["retrieve_job_evidence"].invoke({})

    assert len(ctx.retrieved_chunks) == 2
    assert [c.section for c in ctx.retrieved_chunks] == ["required_skills", "responsibilities"]
    assert "[1] (required_skills)" in message
    assert "(resume was auto-loaded)" in message  # 未先 fetch → lazy 載入有提示


def test_retrieve_job_evidence_rerank_degrades(kit_ctx):
    """rerank 失敗降級向量序：不拋錯、chunks 照存、原因入 ctx.errors（NFR-4）。"""
    ctx = kit_ctx(reranker=_FailingReranker())
    message = _tools_by_name(ctx)["retrieve_job_evidence"].invoke({})

    assert [c.section for c in ctx.retrieved_chunks] == ["required_skills", "responsibilities"]
    assert "Retrieved 2 evidence chunk(s)" in message
    assert any("rerank degraded" in e for e in ctx.errors)


def test_compute_match_tool(kit_ctx):
    """run_matches 重用：MatchResult 存 ctx、摘要含分數與缺失技能。"""
    ctx = kit_ctx()
    message = _tools_by_name(ctx)["compute_match"].invoke({})

    assert ctx.match_result is not None
    assert ctx.match_result.job_id == ctx.job.id
    assert "Match score:" in message
    assert "Go" in message  # 缺 required skill（fake 走 exact-only 降級）


def test_generate_and_save_artifact_flow(kit_ctx, db_session):
    """生成 → payload 入 ctx → save 落 DB（v1）→ 再 save 出 v2（append-only）。"""
    ctx = kit_ctx()
    tools = _tools_by_name(ctx)
    tools["fetch_resume"].invoke({})

    message = tools["generate_cover_letter"].invoke({})
    assert "cover letter draft" in message.lower()
    assert "cover_letter" in ctx.payloads
    assert "(resume was auto-loaded)" not in message  # 已 fetch → 無 lazy 提示

    save_message = tools["save_artifact"].invoke({"kind": "cover_letter"})
    assert "version 1" in save_message
    rows = db_session.scalars(select(GeneratedArtifact)).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.kind == "cover_letter"
    assert row.source == "agent"
    assert row.run_id == ctx.run_id
    assert row.resume_version_id == ctx.version.id
    assert row.content["intro"] == "Dear team,"
    assert ctx.saved["cover_letter"] == row.id

    # append-only：再存一次 → 新 row v2，v1 原封不動。
    save_message = tools["save_artifact"].invoke({"kind": "cover_letter"})
    assert "version 2" in save_message
    rows = db_session.scalars(select(GeneratedArtifact)).all()
    assert sorted(r.version_number for r in rows) == [1, 2]


def test_save_artifact_without_payload(kit_ctx, db_session):
    """未生成就 save → 回提示字串（planner 看得懂）、不落 DB、不拋錯。"""
    ctx = kit_ctx()
    message = _tools_by_name(ctx)["save_artifact"].invoke({"kind": "interview_prep"})

    assert "has not been generated" in message
    assert db_session.scalars(select(GeneratedArtifact)).all() == []
    assert ctx.saved == {}


def test_generate_failure_reports_to_planner(kit_ctx):
    """生成失敗：回 [generation_failed] 訊息、記 ctx.errors、無 payload（NFR-4）。"""
    ctx = kit_ctx(provider=_FakeProvider(kit_error=StructuredOutputError("kit gen down")))
    message = _tools_by_name(ctx)["generate_tailored_resume"].invoke({})

    assert "[generation_failed]" in message
    assert "kit gen down" in message
    assert "(resume was auto-loaded)" in message  # 未先 fetch → lazy 載入提示
    assert "tailored_resume" not in ctx.payloads
    assert any("generate_tailored_resume failed" in e for e in ctx.errors)
