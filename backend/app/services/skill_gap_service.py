"""Skill gap 編排 service（FR-24~30）。

retrieve（pgvector top-k）→ rerank（cross-encoder，失敗降級向量序）→
generate（Gemini structured）→ citation 驗證 → upsert SkillGapReport。

交易不變式同 match_service：所有 LLM 呼叫（``record_call`` 自帶 commit）
都在寫入段之前完成，最後單一 commit。

降級哲學（NFR-4）：檢索與 rerank 是確定性結果、永遠落地；LLM 生成失敗時
``analysis=None``、原因入 ``generation_error``，報告照存、HTTP 照回 200。
"""

import time
import uuid
from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.embeddings.resume_texts import build_resume_embedding_texts
from app.ai.llm.base import LLMError, LLMProvider, StructuredOutputError, TokenUsage
from app.ai.parsers.resume_schema import ResumeParsed
from app.ai.parsers.skill_gap_schema import SkillGapAnalysis
from app.ai.prompts.skill_gap import SKILL_GAP_SYSTEM, build_skill_gap_prompt
from app.ai.rag import rerank as rerank_module
from app.ai.rag.rerank import Reranker, rerank_order
from app.ai.rag.retrieval import TOP_K, RetrievedChunk, retrieve_job_chunks
from app.core.config import settings
from app.db.models import JobChunk, MatchResult, ResumeVersion, SkillGapReport, User
from app.services import job_service, match_scoring, resume_service
from app.services.llm_call_log_service import record_call


class SkillGapResumeNotReadyError(Exception):
    """履歷解析失敗或沒有可用版本，無法分析（router 轉 409）。"""


class SkillGapResumeEmbeddingMissingError(Exception):
    """lazy backfill 後仍無任何履歷向量，無法檢索（router 轉 409）。"""


class SkillGapJobNotIndexedError(Exception):
    """職缺未解析或未向量索引——RAG 無證據可引用（router 轉 409）。

    與 Phase 5「未索引照算分」刻意不同：match 裡 embedding 只是可歸一化的
    成分之一；RAG 裡檢索就是本體，無向量 = 無證據 = 無 citation。
    """


class SkillGapReportNotFoundError(Exception):
    """報告不存在或不屬於此使用者（router 轉 404）。"""


# query 向量的 kind 優先序：skills 最貼近「找職缺要求」的查詢語意；
# 缺漏時退而求其次（與 build_resume_embedding_texts 的 kind 對應）。
_QUERY_KIND_PRIORITY = ("skills", "summary", "experience")

_VALID_SEVERITIES = {"high", "medium", "low"}


# --- 純函式（零 fixture 可測）--------------------------------------------------


def normalize_severity(raw: str) -> str:
    """severity 正規化：strip/lower；不在 {high, medium, low} 一律回 "medium"。"""
    s = raw.strip().lower()
    return s if s in _VALID_SEVERITIES else "medium"


def validate_analysis(
    analysis: SkillGapAnalysis, ranked_chunk_ids: list[uuid.UUID]
) -> dict[str, Any]:
    """Citation 驗證（FR-28 幻覺防護，philosophy 同 ``apply_equivalences``）。

    每條 gap 的 ``evidence_chunk_numbers`` 只接受 1..len(ranked) 的編號，
    映射為 chunk UUID（去重保序）；驗證後零證據的 gap 整條丟棄並計入
    ``dropped_gap_count``——保留無證據的 gap 等於把 FR-28 的違規推給 UI。
    回 ``SkillGapPayload`` 形狀的 dict（存 JSONB）。
    """
    kept: list[dict[str, Any]] = []
    dropped = 0
    for gap in analysis.gaps:
        ids: list[str] = []
        for number in gap.evidence_chunk_numbers:
            if 1 <= number <= len(ranked_chunk_ids):
                chunk_id = str(ranked_chunk_ids[number - 1])
                if chunk_id not in ids:
                    ids.append(chunk_id)
        if not ids:
            dropped += 1
            continue
        kept.append(
            {
                "skill": gap.skill,
                "severity": normalize_severity(gap.severity),
                "reason": gap.reason,
                "evidence_chunk_ids": ids,
                "suggestion": gap.suggestion,
            }
        )
    return {
        "gaps": kept,
        "overall_summary": analysis.overall_summary,
        "dropped_gap_count": dropped,
    }


# --- LLM 呼叫（計時 + 記帳模式同 match_service）--------------------------------


def generate_skill_gap_analysis(
    db: Session,
    *,
    prompt: str,
    provider: LLMProvider,
    user_id: uuid.UUID | None = None,
) -> tuple[SkillGapAnalysis | None, str | None]:
    """RAG 生成（FR-27）：檢索證據 + 履歷事實 → 結構化缺口分析。

    失敗回 ``(None, error)`` 而**不丟例外**——檢索結果照存（NFR-4）。
    回傳的 analysis 未經 citation 驗證，須再過 ``validate_analysis``。
    """
    start = time.perf_counter()
    try:
        result = provider.generate_structured(prompt, SkillGapAnalysis, system=SKILL_GAP_SYSTEM)
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


# --- 主流程 ---------------------------------------------------------------------


def run_skill_gap(
    db: Session,
    *,
    user: User,
    resume_id: uuid.UUID,
    job_id: uuid.UUID,
    provider: LLMProvider,
    reranker: Reranker,
) -> tuple[SkillGapReport, ResumeVersion, list[RetrievedChunk]]:
    """執行完整 skill gap 分析並 upsert 報告（FR-24~30）。

    回 ``(report, version, ranked_chunks)``；ranked_chunks 為 rerank 後順序。
    """
    # 1. Gates：履歷（404 / 409）與職缺（404 / 409）。
    resume = resume_service.get_resume(db, user=user, resume_id=resume_id)
    version = resume_service.get_current_version(db, resume=resume)
    if resume.parse_status != "parsed" or version is None:
        raise SkillGapResumeNotReadyError(str(resume_id))
    job = job_service.get_job(db, user=user, job_id=job_id)
    if job.parsed_data is None or job.index_status != "indexed":
        raise SkillGapJobNotIndexedError(str(job_id))

    # 2. Query 向量：skills 優先；缺漏時 lazy backfill（同 match_service）。
    resume_vecs = resume_service.get_version_embeddings(db, version_id=version.id)
    if not resume_vecs:
        resume_service.generate_resume_embeddings(
            db, version=version, provider=provider, user_id=user.id
        )
        resume_vecs = resume_service.get_version_embeddings(db, version_id=version.id)
    query_kind = next((k for k in _QUERY_KIND_PRIORITY if k in resume_vecs), None)
    if query_kind is None:
        raise SkillGapResumeEmbeddingMissingError(str(resume_id))

    # 3. Retrieve：top-k、job-scoped。index_status 已保證有向量，空結果純防禦。
    retrieved = retrieve_job_chunks(db, job_id=job.id, query_vector=resume_vecs[query_kind])
    if not retrieved:
        raise SkillGapJobNotIndexedError(str(job_id))

    # 4. Rerank：query 文字 = 與 query 向量同一 kind 的 embedding 文字，
    #    讓 bi-encoder 與 cross-encoder 看到同一個查詢。任何失敗降級向量序。
    resume_parsed = ResumeParsed.model_validate(version.parsed_data)
    query_text = next(
        (text for kind, text in build_resume_embedding_texts(resume_parsed) if kind == query_kind),
        "",
    )
    scores: list[float] | None = None
    rerank_error: str | None = None
    try:
        scores = reranker.predict(query_text, [c.content for c in retrieved])
        order = rerank_order(scores, len(retrieved))
        ranked = [retrieved[i] for i in order]
        rerank_used = True
    except Exception as exc:  # 模型載入 / 推論任何失敗都不該讓分析整個掛掉
        ranked = list(retrieved)
        rerank_used = False
        scores = None
        rerank_error = str(exc)

    # 5. Enrichment：同 (resume, job) 且**同履歷版本**的 MatchResult missing
    #    skills 當 hint——版本不同時舊 missing 對新履歷是雜訊；僅進 prompt，
    #    citation 驗證仍是硬防線。
    match_row = db.scalar(
        select(MatchResult).where(MatchResult.resume_id == resume.id, MatchResult.job_id == job.id)
    )
    prior_missing: list[str] = []
    if match_row is not None and match_row.resume_version_id == version.id:
        breakdown = match_row.breakdown or {}
        prior_missing = list(breakdown.get("missing_required", [])) + list(
            breakdown.get("missing_preferred", [])
        )

    # 6. Generate（最後一個 LLM 呼叫；此後才進寫入段）。
    prompt = build_skill_gap_prompt(
        job_title=job.title or "",
        job_company=job.company or "",
        chunks=[(c.section, c.content) for c in ranked],
        resume_skills=match_scoring.resume_skill_pool(resume_parsed),
        resume_summary=resume_parsed.summary,
        resume_years=match_scoring.total_experience_years(
            resume_parsed.experience, now=date.today()
        ),
        prior_missing_skills=prior_missing,
    )
    analysis, generation_error = generate_skill_gap_analysis(
        db, prompt=prompt, provider=provider, user_id=user.id
    )

    # 7. Citation 驗證（LLM 失敗時 payload 留 None）。
    analysis_payload = (
        validate_analysis(analysis, [c.chunk_id for c in ranked]) if analysis is not None else None
    )

    # 8. Retrieval metadata：向量序 chunks（含兩種分數）+ rerank 後序（ER-5）。
    score_by_id = (
        {retrieved[i].chunk_id: float(scores[i]) for i in range(len(retrieved))}
        if scores is not None
        else {}
    )
    retrieval_meta: dict[str, Any] = {
        "query_kind": query_kind,
        "top_k": TOP_K,
        "chunks": [
            {
                "chunk_id": str(c.chunk_id),
                "chunk_index": c.chunk_index,
                "section": c.section,
                "cosine_similarity": c.cosine_similarity,
                "rerank_score": score_by_id.get(c.chunk_id),
            }
            for c in retrieved
        ],
        "ranked_chunk_ids": [str(c.chunk_id) for c in ranked],
        "rerank_used": rerank_used,
        "rerank_model": rerank_module.RERANK_MODEL if rerank_used else None,
        "rerank_error": rerank_error,
    }

    # 9. 寫入段：select-then-update upsert，單一 commit。updated_at 顯式設
    #    func.now()——值全同時 SQLAlchemy 不發 UPDATE，但「上次執行時間」必須
    #    前進（Phase 5 問題 12）。
    report = db.scalar(
        select(SkillGapReport).where(
            SkillGapReport.resume_id == resume.id, SkillGapReport.job_id == job.id
        )
    )
    if report is None:
        report = SkillGapReport(
            user_id=user.id,
            resume_id=resume.id,
            resume_version_id=version.id,
            job_id=job.id,
            retrieval=retrieval_meta,
            analysis=analysis_payload,
            generation_error=generation_error,
        )
        db.add(report)
    else:
        report.resume_version_id = version.id
        report.retrieval = retrieval_meta
        report.analysis = analysis_payload
        report.generation_error = generation_error
        report.updated_at = func.now()  # type: ignore[assignment]
    db.commit()
    db.refresh(report)
    return report, version, ranked


# --- 讀取 -----------------------------------------------------------------------


def get_report(db: Session, *, user: User, report_id: uuid.UUID) -> SkillGapReport:
    """取單筆並驗 owner；非本人或不存在皆丟 SkillGapReportNotFoundError（隔離）。"""
    report = db.get(SkillGapReport, report_id)
    if report is None or report.user_id != user.id:
        raise SkillGapReportNotFoundError(str(report_id))
    return report


def get_report_for_pair(
    db: Session, *, user: User, resume_id: uuid.UUID, job_id: uuid.UUID
) -> SkillGapReport:
    """該 (resume, job) 的既有報告；resume / job 先各自驗 owner（雙重隔離）。"""
    resume = resume_service.get_resume(db, user=user, resume_id=resume_id)
    job = job_service.get_job(db, user=user, job_id=job_id)
    report = db.scalar(
        select(SkillGapReport).where(
            SkillGapReport.resume_id == resume.id, SkillGapReport.job_id == job.id
        )
    )
    if report is None:
        raise SkillGapReportNotFoundError(f"{resume_id}/{job_id}")
    return report


def load_report_chunks(db: Session, *, report: SkillGapReport) -> list[JobChunk]:
    """載入報告引用的 chunks 原文，按 rerank 後順序排回（前端 citation 展開用）。"""
    ranked_ids = [uuid.UUID(cid) for cid in report.retrieval.get("ranked_chunk_ids", [])]
    if not ranked_ids:
        return []
    rows = db.scalars(select(JobChunk).where(JobChunk.id.in_(ranked_ids))).all()
    by_id = {chunk.id: chunk for chunk in rows}
    return [by_id[cid] for cid in ranked_ids if cid in by_id]
