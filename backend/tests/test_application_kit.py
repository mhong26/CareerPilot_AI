"""Application kit 測試：Step 5 生成函式 + Step 6 工具層 + Step 7 graph + Step 8 API。

生成函式（Step 5）：真 DB 記帳、假 provider。
工具層（Step 6）：經 API 種好 user/resume/job（同 test_skill_gaps 的假 provider
正交基底幾何——resume 三段文字 → b0/b1/b2；2-chunk job：responsibilities(b0)、
required_skills(b1)；query kind=skills(b1) → 向量序恆為 [required_skills,
responsibilities]），再直接呼叫 closure 工具斷言 ctx 讀寫與回傳摘要。

graph（Step 7）：ScriptedPlanner 劇本直接 invoke graph，斷言 score routing、
降級、鬼打牆保險絲、re-prompt 與 timeout。

API（Step 8）：kit router 三個注入點全換假（planner 每請求新 ScriptedPlanner，
cursor 不跨請求），端到端斷言 gates、partial、版本控與隔離。
"""

import time
import uuid
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from langgraph.errors import GraphRecursionError
from sqlalchemy import select, text

from app.ai.agents.context import KitRunContext
from app.ai.agents.graph import KIT_RECURSION_LIMIT, build_initial_kit_state, build_kit_graph
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
from app.api.application_kits import get_llm_provider as kits_get_llm_provider
from app.api.application_kits import get_planner_model as kits_get_planner_model
from app.api.application_kits import get_reranker as kits_get_reranker
from app.api.jobs import get_llm_provider as jobs_get_llm_provider
from app.api.resumes import get_llm_provider as resumes_get_llm_provider
from app.db.models import GeneratedArtifact, Job, LLMCallLog, Resume, ResumeVersion, User
from app.main import app
from app.services.application_kit_service import (
    generate_cover_letter_payload,
    generate_interview_prep_payload,
    generate_tailored_resume_payload,
    insert_artifact_version,
)
from tests.agent_fakes import FailingPlanner, ScriptedPlanner, tool_call

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
        cover_error: Exception | None = None,
    ):
        self._tailored = tailored if tailored is not None else _sample_tailored()
        self._cover = cover if cover is not None else _sample_cover()
        self._prep = prep if prep is not None else _sample_prep()
        self._kit_error = kit_error
        self._cover_error = cover_error
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
            if schema is CoverLetterDraft and self._cover_error is not None:
                raise self._cover_error
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
    """失敗：回 (None, error) 不丟例外，LLMCallLog 記一筆 error（NFR-4、FR-56）。"""
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
    """7 個工具、名稱與 FR-48 逐字一致、順序即 SRS 條列順序。"""
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


# --- Step 7：graph -------------------------------------------------------------

_ALL_KINDS = ("tailored_resume", "cover_letter", "interview_prep")


def _invoke_graph(planner, ctx, tools=None, recursion_limit=KIT_RECURSION_LIMIT):
    graph = build_kit_graph(planner, tools if tools is not None else build_kit_tools(ctx), ctx)
    return graph.invoke(
        build_initial_kit_state(job_title="Backend Engineer", job_company="Acme"),
        config={"recursion_limit": recursion_limit},
    )


def _human_prefixed(state, prefix: str) -> list[HumanMessage]:
    return [
        m
        for m in state["messages"]
        if isinstance(m, HumanMessage) and str(m.content).startswith(prefix)
    ]


def _happy_script() -> list[AIMessage]:
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


def _stub_match_tools(ctx: KitRunContext, score: float):
    """只含固定分數 compute_match 的工具組——精準測 score routing 分岔。"""

    @tool
    def compute_match() -> str:
        """Stub: compute the match score."""
        ctx.match_result = SimpleNamespace(match_score=score)  # type: ignore[assignment]
        return f"score {score}"

    return [compute_match]


def test_graph_happy_path_saves_all(kit_ctx, db_session):
    """情境 a：劇本走完 → 三類皆存（同 run_id、v1）、planner 每輪記帳、directive 恰一次。"""
    ctx = kit_ctx()
    planner = ScriptedPlanner(script=_happy_script())
    state = _invoke_graph(planner, ctx)

    assert set(ctx.saved) == set(_ALL_KINDS)
    rows = db_session.scalars(select(GeneratedArtifact)).all()
    assert len(rows) == 3
    assert {r.kind for r in rows} == set(_ALL_KINDS)
    assert {r.run_id for r in rows} == {ctx.run_id}
    assert all(r.version_number == 1 and r.source == "agent" for r in rows)
    # 7 個工具都綁給了 planner（FR-57 的前提）。
    assert len(planner.bound_tools) == 7
    # planner 每輪記帳（8 個劇本步 + 1 次收尾決策 = 9）。
    planner_logs = [log for log in _logs(db_session) if log.operation == "agent_planner"]
    assert len(planner_logs) == 9
    assert all(log.status == "success" for log in planner_logs)
    # compute_match 後分數同步進 state、directive 恰好注入一次。
    assert state["match_score"] == pytest.approx(ctx.match_result.match_score)
    assert state["directive_issued"] is True
    assert len(_human_prefixed(state, "[directive]")) == 1
    assert state["reprompt_count"] == 0


def test_graph_low_score_directive(kit_ctx):
    """情境 b1：score 0.3 → directive 指示先檢索證據；只發一次。"""
    ctx = kit_ctx()
    state = _invoke_graph(
        ScriptedPlanner(script=[tool_call("compute_match", "c1")]),
        ctx,
        tools=_stub_match_tools(ctx, 0.3),
    )

    directives = _human_prefixed(state, "[directive]")
    assert len(directives) == 1
    assert "retrieve_job_evidence" in str(directives[0].content)
    assert state["directive_issued"] is True


def test_graph_high_score_directive(kit_ctx):
    """情境 b2：score 0.9 → directive 為「跳過檢索直接生成」。"""
    ctx = kit_ctx()
    state = _invoke_graph(
        ScriptedPlanner(script=[tool_call("compute_match", "c1")]),
        ctx,
        tools=_stub_match_tools(ctx, 0.9),
    )

    directives = _human_prefixed(state, "[directive]")
    assert len(directives) == 1
    assert "skip extra" in str(directives[0].content)


def test_graph_tool_failure_partial_run(kit_ctx, db_session):
    """情境 c：cover letter 生成失敗 → 其餘兩類照存、reminder 點名缺漏、不 crash。"""
    ctx = kit_ctx(provider=_FakeProvider(cover_error=StructuredOutputError("cover gen down")))
    script = [
        tool_call("fetch_resume", "c1"),
        tool_call("generate_tailored_resume", "c2"),
        tool_call("save_artifact", "c3", {"kind": "tailored_resume"}),
        tool_call("generate_cover_letter", "c4"),
        tool_call("generate_interview_qs", "c5"),
        tool_call("save_artifact", "c6", {"kind": "interview_prep"}),
    ]
    state = _invoke_graph(ScriptedPlanner(script=script), ctx)

    assert set(ctx.saved) == {"tailored_resume", "interview_prep"}
    rows = db_session.scalars(select(GeneratedArtifact)).all()
    assert {r.kind for r in rows} == {"tailored_resume", "interview_prep"}
    assert any("generate_cover_letter failed" in e for e in ctx.errors)
    reminders = _human_prefixed(state, "[reminder]")
    assert len(reminders) == 2  # 重提示打滿上限後 partial 收場
    assert all("cover_letter" in str(m.content) for m in reminders)
    assert state["reprompt_count"] == 2


def test_graph_runaway_hits_recursion_limit(kit_ctx):
    """情境 d：劇本無限重複 fetch_resume → GraphRecursionError 保險絲觸發。"""
    ctx = kit_ctx()
    planner = ScriptedPlanner(script=[tool_call("fetch_resume", "c1")], loop_last=True)
    with pytest.raises(GraphRecursionError):
        _invoke_graph(planner, ctx, recursion_limit=12)


def test_graph_reprompt_recovers(kit_ctx, db_session):
    """情境 e：先只存 2 類就停 → reminder 後補存第 3 類 → 三類齊、reprompt 一次。"""
    ctx = kit_ctx()
    script = [
        tool_call("generate_tailored_resume", "c1"),
        tool_call("save_artifact", "c2", {"kind": "tailored_resume"}),
        tool_call("generate_cover_letter", "c3"),
        tool_call("save_artifact", "c4", {"kind": "cover_letter"}),
        AIMessage(content="Taking a break."),  # 無 tool_calls → 完成檢查發現缺漏
        tool_call("generate_interview_qs", "c5"),
        tool_call("save_artifact", "c6", {"kind": "interview_prep"}),
    ]
    state = _invoke_graph(ScriptedPlanner(script=script), ctx)

    assert set(ctx.saved) == set(_ALL_KINDS)
    assert len(db_session.scalars(select(GeneratedArtifact)).all()) == 3
    reminders = _human_prefixed(state, "[reminder]")
    assert len(reminders) == 1
    assert "interview_prep" in str(reminders[0].content)
    assert state["reprompt_count"] == 1


def test_graph_timeout_short_circuits(kit_ctx, db_session):
    """timeout 防護：deadline 已過 → 不呼叫 planner LLM、直接收尾（partial）。"""
    ctx = kit_ctx()
    ctx.deadline = time.monotonic() - 1.0
    planner = ScriptedPlanner(script=_happy_script())
    state = _invoke_graph(planner, ctx)

    assert state["timed_out"] is True
    assert ctx.saved == {}
    assert len(state["messages"]) == 2  # 只剩初始 system + task，LLM 一次都沒被問
    assert [log for log in _logs(db_session) if log.operation == "agent_planner"] == []


def test_graph_planner_failure_degrades(kit_ctx, db_session):
    """planner 本身壞掉：重試一次仍失敗 → 記 ctx.errors、reminder 打滿後收尾不 crash。"""
    ctx = kit_ctx()
    state = _invoke_graph(FailingPlanner(), ctx)

    assert ctx.saved == {}
    assert sum("planner failed after retry" in e for e in ctx.errors) == 3  # 初始 + 2 次 reminder
    assert state["reprompt_count"] == 2
    assert db_session.scalars(select(GeneratedArtifact)).all() == []


def test_graph_db_error_in_tool_does_not_poison_session(kit_ctx, db_session):
    """工具內 DB 錯誤 → executor rollback → 後續 record_call 與工具照常運作。

    無 rollback 時 psycopg2 交易進入 aborted 狀態，下一次 record_call 直接拋
    PendingRollbackError、降級鏈整條變 500（審查發現 A 的回歸測試）。
    """
    ctx = kit_ctx()

    @tool
    def fetch_resume() -> str:
        """Stub tool that fails mid-transaction with a DB error."""
        ctx.db.execute(text("SELECT * FROM nonexistent_table"))
        return "never"

    script = [tool_call("fetch_resume", "c1"), AIMessage(content="Done.")]
    _invoke_graph(ScriptedPlanner(script=script), ctx, tools=[fetch_resume])

    assert any("fetch_resume failed" in e for e in ctx.errors)
    # 毒化後的 session 已 rollback：後續 planner 記帳全部成功（至少 2 輪 +
    # reprompt 輪）——這行在沒有 rollback 修復時會整個 graph.invoke 炸掉。
    planner_logs = [log for log in _logs(db_session) if log.operation == "agent_planner"]
    assert len(planner_logs) >= 2
    assert all(log.status == "success" for log in planner_logs)


def test_insert_artifact_version_retries_on_conflict(kit_ctx, db_session, monkeypatch):
    """版號並發撞版：unique constraint 擋下 → rollback 重讀重試成功（審查發現 B）。"""
    ctx = kit_ctx()
    kwargs = {
        "user_id": ctx.user.id,
        "resume_id": ctx.resume.id,
        "resume_version_id": ctx.version.id,
        "job_id": ctx.job.id,
        "run_id": ctx.run_id,
        "kind": "cover_letter",
        "source": "agent",
        "content": {"intro": "v1", "body_paragraphs": [], "closing": ""},
    }
    first = insert_artifact_version(db_session, **kwargs)
    assert first.version_number == 1

    # 模擬並發：第一次 max 查詢回傳過期值 0 → 嘗試插 v1 → 撞 unique → 重試讀真值。
    real_scalar = db_session.scalar
    calls = {"n": 0}

    def stale_scalar(stmt):
        calls["n"] += 1
        return 0 if calls["n"] == 1 else real_scalar(stmt)

    monkeypatch.setattr(db_session, "scalar", stale_scalar)
    second = insert_artifact_version(db_session, **kwargs)
    assert second.version_number == 2
    assert calls["n"] == 2  # 確實走了重試路徑


# --- Step 8：service 編排 + API -------------------------------------------------


@pytest.fixture
def kit_api(client, auth):
    """API 整合測試環境：種好 user/resume/job、kit router 三個注入點全換假。

    planner 注入以 factory 呼叫——dependency 每請求執行一次，各請求拿到
    cursor 歸零的新 ScriptedPlanner。
    """

    def _make(provider=None, planner_factory=None, email="user@example.com"):
        provider = provider if provider is not None else _FakeProvider()
        headers = auth(email=email)["headers"]
        for key in (resumes_get_llm_provider, jobs_get_llm_provider, kits_get_llm_provider):
            app.dependency_overrides[key] = lambda p=provider: p
        app.dependency_overrides[kits_get_reranker] = _NoopReranker
        app.dependency_overrides[kits_get_planner_model] = planner_factory or (
            lambda: ScriptedPlanner(script=_happy_script())
        )
        resp = client.post(
            "/resumes/upload",
            data={"text_content": "Jane Smith resume text long enough."},
            headers=headers,
        )
        assert resp.status_code == 201
        resume_id = resp.json()["id"]
        resp = client.post(
            "/jobs", json={"raw_text": "A long enough job description text."}, headers=headers
        )
        assert resp.status_code == 201
        return SimpleNamespace(headers=headers, resume_id=resume_id, job_id=resp.json()["id"])

    return _make


def test_generate_kit_endpoint_happy(kit_api, client, db_session):
    """POST（不帶 resume_id → current resume）：200、三類齊、v1、分數有值。"""
    env = kit_api()
    resp = client.post(f"/jobs/{env.job_id}/generate-application-kit", json={}, headers=env.headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["resume_id"] == env.resume_id
    assert body["missing"] == []
    assert body["errors"] == []
    assert body["match_score"] is not None
    for kind in _ALL_KINDS:
        artifact = body[kind]
        assert artifact is not None
        assert artifact["kind"] == kind
        assert artifact["source"] == "agent"
        assert artifact["version_number"] == 1
        assert artifact["resume_version_number"] == 1
    assert body["cover_letter"]["content"]["intro"] == "Dear team,"
    # 三 rows 同 run_id（一次 run 的產出可歸組，FR-47）。
    assert len({body[kind]["run_id"] for kind in _ALL_KINDS}) == 1
    assert len(db_session.scalars(select(GeneratedArtifact)).all()) == 3


def test_generate_kit_endpoint_explicit_resume_id(kit_api, client):
    """POST 帶明確 resume_id：同樣 200（規劃定案的另一半路徑）。"""
    env = kit_api()
    resp = client.post(
        f"/jobs/{env.job_id}/generate-application-kit",
        json={"resume_id": env.resume_id},
        headers=env.headers,
    )

    assert resp.status_code == 200
    assert resp.json()["resume_id"] == env.resume_id


def test_generate_kit_endpoint_partial(kit_api, client, db_session):
    """情境 c 的 API 面：cover letter 生成失敗 → 200 partial + missing + errors。"""

    def _partial_planner():
        return ScriptedPlanner(
            script=[
                tool_call("fetch_resume", "c1"),
                tool_call("generate_tailored_resume", "c2"),
                tool_call("save_artifact", "c3", {"kind": "tailored_resume"}),
                tool_call("generate_cover_letter", "c4"),
                tool_call("generate_interview_qs", "c5"),
                tool_call("save_artifact", "c6", {"kind": "interview_prep"}),
            ]
        )

    env = kit_api(
        provider=_FakeProvider(cover_error=StructuredOutputError("cover gen down")),
        planner_factory=_partial_planner,
    )
    resp = client.post(f"/jobs/{env.job_id}/generate-application-kit", json={}, headers=env.headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["cover_letter"] is None
    assert body["tailored_resume"] is not None
    assert body["interview_prep"] is not None
    assert body["missing"] == ["cover_letter"]
    assert any("cover gen down" in e for e in body["errors"])
    assert len(db_session.scalars(select(GeneratedArtifact)).all()) == 2


def test_generate_kit_no_resume(client, auth, kit_api):
    """Gate：沒上傳過履歷 → 409（KitResumeNotReadyError）。

    履歷 gate 先於 job 查詢：無履歷的使用者對任何 job_id（存在與否）都拿到
    同一個 409，不洩漏他人 job 的存在性。
    """
    env = kit_api()  # 種好 job；另一個乾淨使用者完全沒有履歷
    headers = auth(email="empty@example.com")["headers"]
    resp = client.post(f"/jobs/{env.job_id}/generate-application-kit", json={}, headers=headers)
    assert resp.status_code == 409
    assert "resume" in resp.json()["detail"].lower()


def test_generate_kit_resume_not_ready(kit_api, client, db_session):
    """Gate：履歷 parse 失敗 → 409 + 明確 detail（agent 一步都不啟動）。"""
    env = kit_api()
    db_session.execute(Resume.__table__.update().values(parse_status="failed"))
    db_session.commit()
    resp = client.post(f"/jobs/{env.job_id}/generate-application-kit", json={}, headers=env.headers)

    assert resp.status_code == 409
    assert "resume" in resp.json()["detail"].lower()
    assert db_session.scalars(select(GeneratedArtifact)).all() == []


def test_generate_kit_job_not_indexed(kit_api, client, db_session):
    """Gate：職缺未索引 → 409（生成與檢索都缺素材）。"""
    env = kit_api()
    db_session.execute(Job.__table__.update().values(index_status="failed"))
    db_session.commit()
    resp = client.post(f"/jobs/{env.job_id}/generate-application-kit", json={}, headers=env.headers)

    assert resp.status_code == 409
    assert "not indexed" in resp.json()["detail"]


def test_generate_kit_job_not_found(kit_api, client):
    """幽靈 job → 404。"""
    env = kit_api()
    resp = client.post(
        f"/jobs/{uuid.uuid4()}/generate-application-kit", json={}, headers=env.headers
    )
    assert resp.status_code == 404


def test_generate_kit_unauthenticated(client):
    """未登入 → 401（FR-5）。"""
    resp = client.post(f"/jobs/{uuid.uuid4()}/generate-application-kit", json={})
    assert resp.status_code == 401


def test_get_kit_before_generation(kit_api, client):
    """GET：尚未生成過 → 404「Application kit not found.」。"""
    env = kit_api()
    resp = client.get(
        f"/jobs/{env.job_id}/application-kit",
        params={"resume_id": env.resume_id},
        headers=env.headers,
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Application kit not found."


def test_get_kit_returns_latest_versions(kit_api, client, db_session):
    """重跑 → v2；GET 各 kind 取最新版（append-only：v1 rows 原封不動）。"""
    env = kit_api()
    for _ in range(2):
        resp = client.post(
            f"/jobs/{env.job_id}/generate-application-kit", json={}, headers=env.headers
        )
        assert resp.status_code == 200

    resp = client.get(
        f"/jobs/{env.job_id}/application-kit",
        params={"resume_id": env.resume_id},
        headers=env.headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["missing"] == []
    assert body["match_score"] is not None  # GET 附既有 MatchResult 分數
    for kind in _ALL_KINDS:
        assert body[kind]["version_number"] == 2
    rows = db_session.scalars(select(GeneratedArtifact)).all()
    assert len(rows) == 6
    assert sorted(r.version_number for r in rows) == [1, 1, 1, 2, 2, 2]


def test_patch_artifact_creates_edit_version(kit_api, client, db_session):
    """PATCH：驗證後插新 row（source=edit、版號+1、沿用 run_id），舊 row 不動。"""
    env = kit_api()
    resp = client.post(f"/jobs/{env.job_id}/generate-application-kit", json={}, headers=env.headers)
    original = resp.json()["cover_letter"]
    new_content = {"intro": "Hi there,", "body_paragraphs": ["Edited."], "closing": "Best."}
    resp = client.patch(
        f"/artifacts/{original['id']}", json={"content": new_content}, headers=env.headers
    )

    assert resp.status_code == 200
    edited = resp.json()
    assert edited["id"] != original["id"]
    assert edited["kind"] == "cover_letter"
    assert edited["source"] == "edit"
    assert edited["version_number"] == 2
    assert edited["run_id"] == original["run_id"]
    assert edited["content"]["intro"] == "Hi there,"
    # 舊 row 原封不動（append-only、歷史不可破壞）。
    row = db_session.get(GeneratedArtifact, uuid.UUID(original["id"]))
    assert row.content["intro"] == "Dear team,"
    assert row.source == "agent"
    # GET 的最新版即編輯版。
    resp = client.get(
        f"/jobs/{env.job_id}/application-kit",
        params={"resume_id": env.resume_id},
        headers=env.headers,
    )
    assert resp.json()["cover_letter"]["id"] == edited["id"]


def test_patch_artifact_invalid_content(kit_api, client, db_session):
    """PATCH 嚴格驗證：型別錯 / 未知鍵 / 缺鍵一律 422、不落新 row。

    編輯路徑不得沿用 LLM 容錯 schema——錯 shape 會被 default 靜默清空成
    空白最新版（審查發現 C 的回歸測試）。
    """
    env = kit_api()
    resp = client.post(f"/jobs/{env.job_id}/generate-application-kit", json={}, headers=env.headers)
    artifact_id = resp.json()["cover_letter"]["id"]
    bad_contents = [
        {"intro": "x", "body_paragraphs": "not-a-list", "closing": "y"},  # 型別錯
        {"intro": "x", "body_paragraphs": [], "closing": "y", "extra_key": 1},  # 未知鍵
        {"intro": "only intro"},  # 缺鍵（會被 default 清空 body/closing）
    ]
    for content in bad_contents:
        resp = client.patch(
            f"/artifacts/{artifact_id}", json={"content": content}, headers=env.headers
        )
        assert resp.status_code == 422, content
    rows = db_session.scalars(select(GeneratedArtifact)).all()
    assert all(r.source == "agent" for r in rows)  # 沒有任何 edit row 被寫入


def test_kit_user_isolation(kit_api, client, auth):
    """隔離（FR-4）：B 對 A 的 job/artifact 一律 404（不可探測）。"""
    env = kit_api()
    resp = client.post(f"/jobs/{env.job_id}/generate-application-kit", json={}, headers=env.headers)
    artifact_id = resp.json()["cover_letter"]["id"]

    other = auth(email="other@example.com")["headers"]
    resp = client.get(
        f"/jobs/{env.job_id}/application-kit",
        params={"resume_id": env.resume_id},
        headers=other,
    )
    assert resp.status_code == 404
    resp = client.patch(
        f"/artifacts/{artifact_id}", json={"content": {"intro": "hijack"}}, headers=other
    )
    assert resp.status_code == 404
