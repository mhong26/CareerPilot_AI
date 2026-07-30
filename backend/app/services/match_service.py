"""Match 編排 service（FR-19~23）。

把「載入履歷向量（必要時 lazy backfill）→ 逐 job 混合計分 → LLM 生成
explanation → upsert MatchResult」封裝成函式；確定性數學全在
``match_scoring``（純函式），本模組只做編排與 LLM / DB 存取。

交易不變式：所有 LLM 呼叫（``record_call`` 自帶 commit）都在寫入段之前完成，
最後單一 commit——中途 crash 只留 log 與 embeddings，不留半套 MatchResult。

Phase 7 的 ``compute_match`` tool 會直接呼叫 ``run_matches``（不經 router），
並以 ``match_score`` 的 0.5 / 0.8 門檻路由。
"""

import time
import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.llm.base import LLMError, LLMProvider, StructuredOutputError, TokenUsage
from app.ai.parsers.job_schema import JobParsed
from app.ai.parsers.match_schema import MatchExplanation, SkillEquivalenceResult
from app.ai.parsers.resume_schema import ResumeParsed
from app.ai.prompts.match import (
    MATCH_EXPLANATION_SYSTEM,
    SKILL_EQUIVALENCE_SYSTEM,
    build_match_explanation_prompt,
    build_skill_equivalence_prompt,
)
from app.core.config import settings
from app.db.models import Job, JobChunk, JobEmbedding, MatchResult, ResumeVersion, User
from app.services import match_scoring, resume_service
from app.services.llm_call_log_service import record_call


class MatchResumeNotReadyError(Exception):
    """履歷解析失敗或沒有可用版本，無法執行匹配（router 轉 409）。"""


@dataclass
class _ComputedMatch:
    """寫入段之前的記憶體結果（LLM 全部呼叫完才進 DB）。"""

    job: Job
    score: float
    breakdown: dict[str, Any]
    explanation: dict[str, Any] | None
    explanation_error: str | None


# --- LLM 呼叫（計時 + 記帳模式同 parse_job_text）------------------------------


def find_skill_equivalences(
    db: Session,
    *,
    job_skills: list[str],
    resume_skills: list[str],
    provider: LLMProvider,
    user_id: uuid.UUID | None = None,
) -> tuple[list[tuple[str, str]] | None, str | None]:
    """LLM 語意等價 fallback（FR-20）：exact 比對剩下的技能一次 batch 配對。

    失敗回 ``(None, error)`` 而**不丟例外**——呼叫端降級為只用 exact 結果。
    回傳的 pairs 未經驗證，須再過 ``match_scoring.apply_equivalences``。
    """
    prompt = build_skill_equivalence_prompt(job_skills, resume_skills)
    start = time.perf_counter()
    try:
        result = provider.generate_structured(
            prompt, SkillEquivalenceResult, system=SKILL_EQUIVALENCE_SYSTEM
        )
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
    return [(p.job_skill, p.resume_skill) for p in result.data.equivalences], None


def generate_match_explanation(
    db: Session,
    *,
    job: Job,
    match_score: float,
    breakdown: dict[str, Any],
    resume_summary: str,
    provider: LLMProvider,
    user_id: uuid.UUID | None = None,
) -> tuple[MatchExplanation | None, str | None]:
    """把算好的結構化事實轉述成 explanation（FR-22；LLM 不重新評估）。

    失敗回 ``(None, error)``——分數照存、explanation 留空（NFR-4）。
    """
    prompt = build_match_explanation_prompt(
        job_title=job.title or "",
        job_company=job.company or "",
        match_score=match_score,
        breakdown=breakdown,
        resume_summary=resume_summary,
    )
    start = time.perf_counter()
    try:
        result = provider.generate_structured(
            prompt, MatchExplanation, system=MATCH_EXPLANATION_SYSTEM
        )
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


# --- 計分（記憶體內，純函式組合）----------------------------------------------


def _score_job(
    db: Session,
    *,
    job: Job,
    resume_parsed: ResumeParsed,
    skill_pool: list[str],
    resume_years: float | None,
    resume_vecs: dict[str, list[float]],
    chunk_vecs: list[tuple[str, Any]],
    provider: LLMProvider,
    user_id: uuid.UUID,
) -> tuple[float, dict[str, Any]]:
    """單一 job 的混合計分：exact 技能 →（必要時）LLM 等價 → 各成分 → 加權合成。"""
    job_parsed = JobParsed.model_validate(job.parsed_data)

    # 技能覆蓋：exact 層先行，剩餘的一次 LLM 等價（失敗降級為只用 exact）。
    # required 為空時回退 qualifications（phase5_notes 問題 6）。
    matched_req, missing_req = match_scoring.match_skills(
        match_scoring.job_required_skills(job_parsed), skill_pool
    )
    matched_pref, missing_pref = match_scoring.match_skills(job_parsed.preferred_skills, skill_pool)
    kept_pairs: list[tuple[str, str]] = []
    llm_equivalence_used = False
    unmatched = missing_req + missing_pref
    if unmatched and skill_pool:
        pairs, _error = find_skill_equivalences(
            db, job_skills=unmatched, resume_skills=skill_pool, provider=provider, user_id=user_id
        )
        if pairs is not None:
            llm_equivalence_used = True
            extra_req, missing_req, kept_req = match_scoring.apply_equivalences(
                missing_req, pairs, skill_pool
            )
            extra_pref, missing_pref, kept_pref = match_scoring.apply_equivalences(
                missing_pref, pairs, skill_pool
            )
            matched_req = matched_req + extra_req
            matched_pref = matched_pref + extra_pref
            kept_pairs = kept_req + kept_pref

    required_coverage = match_scoring.coverage(
        len(matched_req), len(matched_req) + len(missing_req)
    )
    preferred_coverage = match_scoring.coverage(
        len(matched_pref), len(matched_pref) + len(missing_pref)
    )

    # 語意相似：履歷各 kind 向量 vs 該 job 全部 chunk 向量。
    emb_sim = match_scoring.embedding_similarity(resume_vecs, [v for _, v in chunk_vecs])

    # 年資 + 職稱（overview chunk 專為職稱相似度而建，見 chunking.py）。
    required_years = match_scoring.extract_required_years(job_parsed.experience_requirements)
    yrs = match_scoring.years_score(resume_years, required_years)
    title_sim: float | None = None
    overview_vec = next((v for s, v in chunk_vecs if s == "overview"), None)
    experience_vec = resume_vecs.get("experience")
    if overview_vec is not None and experience_vec is not None:
        title_sim = match_scoring.rescale_similarity(
            match_scoring.cosine(experience_vec, overview_vec)
        )
    exp_align = match_scoring.experience_alignment(yrs, title_sim)

    components: dict[str, float | None] = {
        "embedding_similarity": emb_sim,
        "required_coverage": required_coverage,
        "preferred_coverage": preferred_coverage,
        "experience_alignment": exp_align,
    }
    score, weights_used = match_scoring.compose_match_score(components)
    breakdown: dict[str, Any] = {
        **components,
        "matched_required": matched_req,
        "missing_required": missing_req,
        "matched_preferred": matched_pref,
        "missing_preferred": missing_pref,
        "equivalent_pairs": [{"job_skill": j, "resume_skill": r} for j, r in kept_pairs],
        "llm_equivalence_used": llm_equivalence_used,
        "resume_years": resume_years,
        "required_years": required_years,
        "years_score": yrs,
        "title_similarity": title_sim,
        "weights_used": weights_used,
    }
    return score, breakdown


# --- 主流程 --------------------------------------------------------------------


def run_matches(
    db: Session,
    *,
    user: User,
    resume_id: uuid.UUID,
    job_ids: list[uuid.UUID],
    provider: LLMProvider,
) -> tuple[ResumeVersion, list[tuple[MatchResult, Job]], list[tuple[uuid.UUID, str]]]:
    """批次匹配：計分 + explanation 全算完才 upsert（FR-19~23）。

    回 ``(使用的履歷版本, 按分數 desc 的 (MatchResult, Job), skipped)``；
    skipped 每項為 ``(job_id, reason)``，reason ∈ {"not_found", "not_parsed"}。
    找不到 / 非本人 resume 丟 ``ResumeNotFoundError``；履歷未解析丟
    ``MatchResumeNotReadyError``。
    """
    resume = resume_service.get_resume(db, user=user, resume_id=resume_id)
    version = resume_service.get_current_version(db, resume=resume)
    if resume.parse_status != "parsed" or version is None:
        raise MatchResumeNotReadyError(str(resume_id))

    # Lazy backfill：儲存時 embedding 失敗過的履歷在此再試一次；
    # 仍失敗 → embedding 成分缺失、權重歸一化（NFR-4），照樣計分。
    resume_vecs = resume_service.get_version_embeddings(db, version_id=version.id)
    if not resume_vecs:
        resume_service.generate_resume_embeddings(
            db, version=version, provider=provider, user_id=user.id
        )
        resume_vecs = resume_service.get_version_embeddings(db, version_id=version.id)

    # 逐 job 分類：非本人 / 不存在與未解析的跳過並回報，不整包失敗（NFR-4）。
    unique_ids = list(dict.fromkeys(job_ids))
    jobs_by_id = {
        job.id: job
        for job in db.scalars(select(Job).where(Job.id.in_(unique_ids), Job.user_id == user.id))
    }
    skipped: list[tuple[uuid.UUID, str]] = []
    scorable: list[Job] = []
    for job_id in unique_ids:
        job = jobs_by_id.get(job_id)
        if job is None:
            skipped.append((job_id, "not_found"))
        elif job.parsed_data is None:
            skipped.append((job_id, "not_parsed"))
        else:
            scorable.append(job)  # 未索引（無向量）照樣計分，只缺 embedding 成分

    # 一次載入所有 scorable jobs 的 (section, vector)。
    vectors_by_job: dict[uuid.UUID, list[tuple[str, Any]]] = {}
    if scorable:
        rows = db.execute(
            select(JobEmbedding.job_id, JobChunk.section, JobEmbedding.vector)
            .join(JobChunk, JobChunk.id == JobEmbedding.chunk_id)
            .where(JobEmbedding.job_id.in_([job.id for job in scorable]))
        ).all()
        for job_id, section, vector in rows:
            vectors_by_job.setdefault(job_id, []).append((section, vector))

    resume_parsed = ResumeParsed.model_validate(version.parsed_data)
    skill_pool = match_scoring.resume_skill_pool(resume_parsed)
    resume_years = match_scoring.total_experience_years(resume_parsed.experience, now=date.today())

    computed: list[_ComputedMatch] = []
    for job in scorable:
        score, breakdown = _score_job(
            db,
            job=job,
            resume_parsed=resume_parsed,
            skill_pool=skill_pool,
            resume_years=resume_years,
            resume_vecs=resume_vecs,
            chunk_vecs=vectors_by_job.get(job.id, []),
            provider=provider,
            user_id=user.id,
        )
        explanation, explanation_error = generate_match_explanation(
            db,
            job=job,
            match_score=score,
            breakdown=breakdown,
            resume_summary=resume_parsed.summary,
            provider=provider,
            user_id=user.id,
        )
        computed.append(
            _ComputedMatch(
                job=job,
                score=score,
                breakdown=breakdown,
                explanation=explanation.model_dump() if explanation is not None else None,
                explanation_error=explanation_error,
            )
        )

    # 寫入段（此後不再有 LLM 呼叫）：select-then-update upsert，單一 commit。
    # updated_at 一律顯式更新（用 DB 端 now()，時鐘單一來源）——重跑值完全
    # 相同時 ORM 不會發 UPDATE，但「上次執行時間」仍須前進。
    existing = {
        row.job_id: row
        for row in db.scalars(
            select(MatchResult).where(
                MatchResult.resume_id == resume.id,
                MatchResult.job_id.in_([c.job.id for c in computed]),
            )
        )
    }
    results: list[tuple[MatchResult, Job]] = []
    for c in computed:
        row = existing.get(c.job.id)
        if row is None:
            row = MatchResult(
                user_id=user.id,
                resume_id=resume.id,
                resume_version_id=version.id,
                job_id=c.job.id,
                match_score=c.score,
                breakdown=c.breakdown,
                explanation=c.explanation,
                explanation_error=c.explanation_error,
            )
            db.add(row)
        else:
            row.resume_version_id = version.id
            row.match_score = c.score
            row.breakdown = c.breakdown
            row.explanation = c.explanation
            row.explanation_error = c.explanation_error
            row.updated_at = func.now()  # type: ignore[assignment]
        results.append((row, c.job))
    db.commit()

    results.sort(key=lambda pair: pair[0].match_score, reverse=True)
    return version, results, skipped


def list_matches(db: Session, *, user: User, resume_id: uuid.UUID) -> list[tuple[MatchResult, Job]]:
    """該履歷的全部匹配結果，按分數 desc（FR-21/23；先驗 owner，隔離）。"""
    resume = resume_service.get_resume(db, user=user, resume_id=resume_id)
    return list(
        db.execute(
            select(MatchResult, Job)
            .join(Job, Job.id == MatchResult.job_id)
            .where(MatchResult.resume_id == resume.id)
            .order_by(MatchResult.match_score.desc())
        ).tuples()
    )
