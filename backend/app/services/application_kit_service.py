"""Application kit service（FR-31~44、FR-45~49、FR-56）。

分兩層：三個 artifact 的「call + log + 降級」生成函式（工具層呼叫），與
agent 編排（``run_application_kit``：gates → ctx → tools → graph → 收集
結果）、artifact 讀取／編輯（``get_latest_kit`` / ``update_artifact``）。

交易注意：``record_call`` 自帶 commit。agent run 天生交替「LLM 呼叫」與
「寫入」，無法維持 skill_gap_service 式的「單一最終 commit」不變式——
改以 ``save_artifact`` 逐 artifact commit（每個 artifact 是獨立原子單位，
run 中途失敗時已保存者仍有效，NFR-4）。

降級哲學（NFR-4）：LLM 生成失敗回 ``(None, error)`` 不丟例外，成功／失敗
都入 ``LLMCallLog``（malformed rate 與成本追蹤自動涵蓋，FR-56、FR-59）。
"""

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, TypeVar

from langchain_core.language_models import BaseChatModel
from langgraph.errors import GraphRecursionError
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.ai.llm.base import LLMError, LLMProvider, StructuredOutputError, TokenUsage
from app.ai.parsers.kit_schema import (
    CoverLetterDraft,
    InterviewPrepSet,
    TailoredResumeSuggestions,
)
from app.ai.prompts.kit import (
    COVER_LETTER_SYSTEM,
    INTERVIEW_QS_SYSTEM,
    TAILORED_RESUME_SYSTEM,
)
from app.ai.rag.rerank import Reranker
from app.core.config import settings
from app.db.models import GeneratedArtifact, Job, MatchResult, Resume, ResumeVersion, User
from app.services import job_service, resume_service
from app.services.llm_call_log_service import record_call

T = TypeVar("T", bound=BaseModel)


class KitResumeNotReadyError(Exception):
    """沒有可用履歷（未上傳、解析失敗或無版本），無法生成（router 轉 409）。"""


class KitJobNotIndexedError(Exception):
    """職缺未解析或未向量索引——生成與檢索都缺素材（router 轉 409）。"""


class ArtifactNotFoundError(Exception):
    """Artifact 不存在或不屬於此使用者（router 轉 404，隔離不可探測）。"""


class ArtifactContentInvalidError(Exception):
    """編輯的 content 鍵集不符 kind schema（router 轉 422）。

    編輯路徑刻意比 LLM 生成路徑嚴格：kit_schema 全欄位有 default 且忽略
    extra 是為了容忍 LLM 的部分輸出；沿用到 PATCH 會讓錯 shape 的 content
    靜默變成一版空白 artifact。PATCH 語意是「完整 content 取代」——未知鍵
    與缺鍵都拒絕，型別錯誤則由 pydantic ``ValidationError`` 把關。
    """


# kind → content 的驗證 schema（save 與 update 共用；SRS §5.3.3）。
KIND_SCHEMAS: dict[str, type[BaseModel]] = {
    "tailored_resume": TailoredResumeSuggestions,
    "cover_letter": CoverLetterDraft,
    "interview_prep": InterviewPrepSet,
}


def _generate_payload(
    db: Session,
    *,
    prompt: str,
    system: str,
    schema: type[T],
    provider: LLMProvider,
    user_id: uuid.UUID | None = None,
) -> tuple[T | None, str | None]:
    """單次 structured 生成：計時 → 生成 → 記帳 → 失敗回 ``(None, error)`` 不丟例外。

    與 ``skill_gap_service.generate_skill_gap_analysis`` 同模板，抽參數消除三份重複。
    """
    start = time.perf_counter()
    try:
        result = provider.generate_structured(prompt, schema, system=system)
    except (StructuredOutputError, LLMError) as exc:
        record_call(
            db,
            provider="gemini",
            model=getattr(exc, "model", "") or getattr(provider, "model", settings.gemini_model),
            operation="generate_structured",
            prompt=prompt,
            usage=getattr(exc, "usage", None) or TokenUsage(),
            latency_ms=int((time.perf_counter() - start) * 1000),
            status="error",
            error=str(exc),
            user_id=user_id,
            attempts=getattr(exc, "attempts", 1),
            repair_used=getattr(exc, "repair_used", False),
            fallback_used=getattr(exc, "fallback_used", False),
            cost=getattr(exc, "cost_estimate", None),
        )
        return None, str(exc)

    record_call(
        db,
        provider="gemini",
        model=result.model,
        operation="generate_structured",
        prompt=prompt,
        usage=result.usage,
        latency_ms=int((time.perf_counter() - start) * 1000),
        status="success",
        user_id=user_id,
        attempts=result.attempts,
        repair_used=result.repair_used,
        fallback_used=result.fallback_used,
        cost=result.cost_estimate,
    )
    return result.data, None


def generate_tailored_resume_payload(
    db: Session,
    *,
    prompt: str,
    provider: LLMProvider,
    user_id: uuid.UUID | None = None,
) -> tuple[TailoredResumeSuggestions | None, str | None]:
    """客製化履歷建議（FR-31~35）；prompt 由 caller 以 ``build_tailored_resume_prompt`` 建好。"""
    return _generate_payload(
        db,
        prompt=prompt,
        system=TAILORED_RESUME_SYSTEM,
        schema=TailoredResumeSuggestions,
        provider=provider,
        user_id=user_id,
    )


def generate_cover_letter_payload(
    db: Session,
    *,
    prompt: str,
    provider: LLMProvider,
    user_id: uuid.UUID | None = None,
) -> tuple[CoverLetterDraft | None, str | None]:
    """Cover letter 草稿（FR-37~39）；prompt 由 caller 以 ``build_cover_letter_prompt`` 建好。"""
    return _generate_payload(
        db,
        prompt=prompt,
        system=COVER_LETTER_SYSTEM,
        schema=CoverLetterDraft,
        provider=provider,
        user_id=user_id,
    )


def generate_interview_prep_payload(
    db: Session,
    *,
    prompt: str,
    provider: LLMProvider,
    user_id: uuid.UUID | None = None,
) -> tuple[InterviewPrepSet | None, str | None]:
    """面試準備題組（FR-41~43）；prompt 由 caller 以 ``build_interview_qs_prompt`` 建好。"""
    return _generate_payload(
        db,
        prompt=prompt,
        system=INTERVIEW_QS_SYSTEM,
        schema=InterviewPrepSet,
        provider=provider,
        user_id=user_id,
    )


def insert_artifact_version(
    db: Session,
    *,
    user_id: uuid.UUID,
    resume_id: uuid.UUID,
    resume_version_id: uuid.UUID,
    job_id: uuid.UUID,
    run_id: uuid.UUID,
    kind: str,
    source: str,
    content: dict[str, Any],
) -> GeneratedArtifact:
    """插入同 (resume, job, kind) 的下一個版號 row 並 commit（append-only 唯一寫入點）。

    select-max+1 在並發下會撞版——由 ``uq_generated_artifacts_pair_kind_version``
    擋下，這裡捕捉 ``IntegrityError`` 後 rollback 重讀重試一次（兩個寫入端：
    agent 的 save_artifact 工具與使用者的 PATCH 編輯）。
    """
    last_exc: IntegrityError | None = None
    for _ in range(2):
        current_max = (
            db.scalar(
                select(func.max(GeneratedArtifact.version_number)).where(
                    GeneratedArtifact.resume_id == resume_id,
                    GeneratedArtifact.job_id == job_id,
                    GeneratedArtifact.kind == kind,
                )
            )
            or 0
        )
        artifact = GeneratedArtifact(
            user_id=user_id,
            resume_id=resume_id,
            resume_version_id=resume_version_id,
            job_id=job_id,
            run_id=run_id,
            kind=kind,
            source=source,
            version_number=current_max + 1,
            content=content,
        )
        db.add(artifact)
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            last_exc = exc
            continue
        db.refresh(artifact)
        return artifact
    assert last_exc is not None
    raise last_exc


# --- Agent 編排（Step 8）--------------------------------------------------------


@dataclass
class KitRunOutcome:
    """``run_application_kit`` 的結果包——router 組 response 所需的一切。"""

    resume: Resume
    version: ResumeVersion
    job: Job
    run_id: uuid.UUID
    # kind → 本次 run 保存的 GeneratedArtifact（partial 時缺 key）。
    artifacts: dict[str, GeneratedArtifact]
    match_score: float | None
    missing: list[str]
    errors: list[str] = field(default_factory=list)


def _resolve_resume(
    db: Session, *, user: User, resume_id: uuid.UUID | None
) -> tuple[Resume, ResumeVersion]:
    """解析履歷 + parse gate。

    與 skill-gap 的「強制 resume_id」刻意不同：kit 的 ``resume_id`` 可省略、
    預設 current resume（規劃定案）——「對這個職缺做申請包」的心智模型裡
    履歷幾乎總是「我現在這份」。
    """
    if resume_id is not None:
        resume = resume_service.get_resume(db, user=user, resume_id=resume_id)
    else:
        maybe = resume_service.get_current_resume(db, user=user)
        if maybe is None:
            raise KitResumeNotReadyError("no resume uploaded")
        resume = maybe
    version = resume_service.get_current_version(db, resume=resume)
    if resume.parse_status != "parsed" or version is None:
        raise KitResumeNotReadyError(str(resume.id))
    return resume, version


def run_application_kit(
    db: Session,
    *,
    user: User,
    resume_id: uuid.UUID | None,
    job_id: uuid.UUID,
    provider: LLMProvider,
    reranker: Reranker,
    planner_model: BaseChatModel,
) -> KitRunOutcome:
    """跑 kit agent（FR-47~49、FR-57）：gates → graph → 收集已保存 artifacts。

    Agent 啟動前先把必敗的 run 擋在 gates（409 語意），省 LLM 成本；啟動後
    任何失敗都降級（partial 結果照回，NFR-4）——含 recursion limit 保險絲。
    """
    # 延遲匯入斷循環：agents.tools 反向依賴本模組的生成函式。
    from app.ai.agents.context import KIT_KINDS, KitRunContext
    from app.ai.agents.graph import (
        KIT_DEADLINE_SECONDS,
        KIT_RECURSION_LIMIT,
        build_initial_kit_state,
        build_kit_graph,
    )
    from app.ai.agents.tools import build_kit_tools

    resume, version = _resolve_resume(db, user=user, resume_id=resume_id)
    job = job_service.get_job(db, user=user, job_id=job_id)
    if job.parsed_data is None or job.index_status != "indexed":
        raise KitJobNotIndexedError(str(job_id))

    ctx = KitRunContext(
        db=db,
        user=user,
        resume=resume,
        version=version,
        job=job,
        provider=provider,
        reranker=reranker,
        run_id=uuid.uuid4(),
        deadline=time.monotonic() + KIT_DEADLINE_SECONDS,
    )
    graph = build_kit_graph(planner_model, build_kit_tools(ctx), ctx)
    try:
        graph.invoke(
            build_initial_kit_state(job_title=job.title or "", job_company=job.company or ""),
            config={"recursion_limit": KIT_RECURSION_LIMIT},
        )
    except GraphRecursionError:
        ctx.errors.append(
            f"agent stopped at the {KIT_RECURSION_LIMIT}-step safety limit; "
            "returning whatever was saved so far"
        )
    except Exception as exc:  # noqa: BLE001 — 保底（NFR-4）：非預期失敗仍回 partial
        # save_artifact 逐件 commit，已保存的 artifacts 不受 rollback 影響；
        # rollback 讓（可能已毒化的）session 回到可用狀態供下方收集結果。
        db.rollback()
        ctx.errors.append(f"agent run aborted unexpectedly: {exc}")

    artifacts: dict[str, GeneratedArtifact] = {}
    for kind, artifact_id in ctx.saved.items():
        artifact = db.get(GeneratedArtifact, artifact_id)
        if artifact is not None:
            artifacts[kind] = artifact
    return KitRunOutcome(
        resume=resume,
        version=version,
        job=job,
        run_id=ctx.run_id,
        artifacts=artifacts,
        match_score=(ctx.match_result.match_score if ctx.match_result is not None else None),
        missing=[k for k in KIT_KINDS if k not in artifacts],
        errors=list(ctx.errors),
    )


def get_latest_kit(
    db: Session, *, user: User, resume_id: uuid.UUID, job_id: uuid.UUID
) -> tuple[Resume, Job, dict[str, GeneratedArtifact], float | None]:
    """該 (resume, job) 各 kind 的最新版 artifact + 既有 match 分數（顯示用）。

    回 ``(resume, job, artifacts, match_score)``；三類全無時 artifacts 為空
    dict（router 轉 404「尚未生成」）。ownership 由 get_resume/get_job 把關。
    """
    resume = resume_service.get_resume(db, user=user, resume_id=resume_id)
    job = job_service.get_job(db, user=user, job_id=job_id)
    artifacts: dict[str, GeneratedArtifact] = {}
    for kind in KIND_SCHEMAS:
        artifact = db.scalar(
            select(GeneratedArtifact)
            .where(
                GeneratedArtifact.resume_id == resume.id,
                GeneratedArtifact.job_id == job.id,
                GeneratedArtifact.kind == kind,
            )
            # created_at 為防禦性 tiebreak（unique constraint 下版號不應重複）。
            .order_by(GeneratedArtifact.version_number.desc(), GeneratedArtifact.created_at.desc())
            .limit(1)
        )
        if artifact is not None:
            artifacts[kind] = artifact
    match = db.scalar(
        select(MatchResult).where(MatchResult.resume_id == resume.id, MatchResult.job_id == job.id)
    )
    return resume, job, artifacts, (match.match_score if match is not None else None)


def update_artifact(
    db: Session, *, user: User, artifact_id: uuid.UUID, content: dict[str, Any]
) -> GeneratedArtifact:
    """使用者編輯版（FR-40、FR-45）：驗證 content → **插入新 row**（append-only）。

    永不 UPDATE 舊 row——歷史不可破壞（SRS §5.3.4）。新 row 沿用原 pair /
    kind / run_id 與履歷版本快照，``source="edit"``、版號 +1。content 不符
    kind schema 時讓 pydantic ``ValidationError`` 上拋（router 轉 422）。
    """
    original = db.scalar(
        select(GeneratedArtifact).where(
            GeneratedArtifact.id == artifact_id, GeneratedArtifact.user_id == user.id
        )
    )
    if original is None:
        raise ArtifactNotFoundError(str(artifact_id))
    schema = KIND_SCHEMAS[original.kind]
    unknown = set(content) - set(schema.model_fields)
    missing = set(schema.model_fields) - set(content)
    if unknown or missing:
        raise ArtifactContentInvalidError(
            f"unknown keys: {sorted(unknown)}; missing keys: {sorted(missing)}"
        )
    validated = schema.model_validate(content)
    return insert_artifact_version(
        db,
        user_id=user.id,
        resume_id=original.resume_id,
        resume_version_id=original.resume_version_id,
        job_id=original.job_id,
        run_id=original.run_id,
        kind=original.kind,
        source="edit",
        content=validated.model_dump(),
    )
