"""7 個 agent 工具（FR-57，名稱與 SRS 逐字一致）＋ closure 工廠 ``build_kit_tools``。

兩個關鍵設計（詳見 context.py）：
- **Closure 工廠**：工具經 ``@tool`` 暴露給 LLM 的參數表只剩業務參數；db /
  user / provider 等資源由 closure 捕獲的 ctx 供應，LLM 碰不到。
- **精簡回傳原則**：完整產物寫進 ctx，回給 LLM 的字串只放幾百字摘要。

工具的 **docstring 會作為工具說明書送給 planner LLM**，故用英文撰寫（同
schema ``Field(description=...)`` 慣例）。

錯誤策略：資源類錯誤（DB、embedding 缺失…）直接拋出，由 Step 7 的
executor node 統一轉成錯誤 ToolMessage 降級——單一職責；LLM 生成類已有
``(None, error)`` 慣例，在工具內轉為錯誤訊息字串並記入 ``ctx.errors``。
"""

from datetime import date
from typing import Any, Literal

from langchain_core.tools import BaseTool, tool
from sqlalchemy import select

from app.ai.agents.context import KitRunContext
from app.ai.embeddings.resume_texts import build_resume_embedding_texts
from app.ai.parsers.resume_schema import ResumeParsed
from app.ai.prompts.kit import (
    build_cover_letter_prompt,
    build_interview_qs_prompt,
    build_tailored_resume_prompt,
)
from app.ai.rag.rerank import rerank_order
from app.ai.rag.retrieval import TOP_K, retrieve_job_chunks
from app.db.models import SkillGapReport
from app.services import match_scoring, match_service, resume_service
from app.services.application_kit_service import (
    generate_cover_letter_payload,
    generate_interview_prep_payload,
    generate_tailored_resume_payload,
    insert_artifact_version,
)

# query 向量的 kind 優先序（同 skill_gap_service._QUERY_KIND_PRIORITY）。
_QUERY_KIND_PRIORITY = ("skills", "summary", "experience")
# ToolMessage 摘要中每個證據 chunk 的截斷長度——精簡回傳原則。
_SUMMARY_CHUNK_CHARS = 200
# ToolMessage 摘要中技能清單的上限。
_SUMMARY_SKILLS = 10


def _ensure_resume_parsed(ctx: KitRunContext) -> tuple[ResumeParsed, str]:
    """回 ``(parsed, note)``；planner 沒先 fetch 時 lazy 載入並回提示前綴。"""
    if ctx.resume_parsed is None:
        ctx.resume_parsed = ResumeParsed.model_validate(ctx.version.parsed_data)
        return ctx.resume_parsed, "(resume was auto-loaded) "
    return ctx.resume_parsed, ""


def _resume_sections(parsed: ResumeParsed) -> list[tuple[str, str]]:
    """把 experience / projects 組成 ``(section 名, 原文區塊)``——bullet rewrite 素材。

    一份 role / project 一個 tuple：prompt 端的截斷（``_MAX_SECTION_CHARS`` /
    ``_MAX_SECTIONS``）逐塊套用，長履歷不會因整類擠成一大塊而被砍掉大半。
    """
    sections: list[tuple[str, str]] = []
    for item in parsed.experience:
        header = f"{item.title} at {item.company} ({item.start_date} - {item.end_date})"
        block = "\n".join([header, *(f"- {b}" for b in item.bullets)])
        sections.append((f"experience: {item.title} at {item.company}", block))
    for project in parsed.projects:
        lines = [project.name]
        if project.description:
            lines.append(project.description)
        lines.extend(f"- {b}" for b in project.bullets)
        if project.tech:
            lines.append("Tech: " + ", ".join(project.tech))
        sections.append((f"project: {project.name}", "\n".join(lines)))
    return sections


def _breakdown_skills(ctx: KitRunContext, *keys: str) -> list[str]:
    """從 match breakdown 取技能清單；尚未 compute_match 時回空（optional-friendly）。"""
    if ctx.match_result is None:
        return []
    breakdown = ctx.match_result.breakdown or {}
    out: list[str] = []
    for key in keys:
        out.extend(breakdown.get(key) or [])
    return out


def _gap_hints(ctx: KitRunContext) -> list[str]:
    """同 pair 既有 SkillGapReport 的缺口技能——重用 Phase 6 成果，不強制。

    version 守門同 skill_gap_service 對 match hint 的慣例：報告是舊履歷版本
    的分析就不當 hint。
    """
    report = ctx.db.scalar(
        select(SkillGapReport).where(
            SkillGapReport.resume_id == ctx.resume.id, SkillGapReport.job_id == ctx.job.id
        )
    )
    if report is None or report.resume_version_id != ctx.version.id or not report.analysis:
        return []
    return [g.get("skill", "") for g in report.analysis.get("gaps", []) if g.get("skill")]


def _generation_kwargs(ctx: KitRunContext, parsed: ResumeParsed) -> dict[str, Any]:
    """三個 generate 工具共用的 prompt builder 參數。"""
    return {
        "job_title": ctx.job.title or "",
        "job_company": ctx.job.company or "",
        "resume_skills": match_scoring.resume_skill_pool(parsed),
        "resume_summary": parsed.summary,
        "resume_years": match_scoring.total_experience_years(parsed.experience, now=date.today()),
        "resume_sections": _resume_sections(parsed),
        "evidence_chunks": [(c.section, c.content) for c in ctx.retrieved_chunks],
        "missing_skills": _breakdown_skills(ctx, "missing_required", "missing_preferred"),
    }


def build_kit_tools(ctx: KitRunContext) -> list[BaseTool]:
    """建出 7 個綁定 ctx 的工具（FR-57）；順序即 SRS 條列順序。"""

    @tool
    def fetch_resume() -> str:
        """Load the candidate's parsed resume into working memory. Call this before
        generating any artifact so suggestions are grounded in the real resume."""
        ctx.resume_parsed = ResumeParsed.model_validate(ctx.version.parsed_data)
        parsed = ctx.resume_parsed
        skills = match_scoring.resume_skill_pool(parsed)
        years = match_scoring.total_experience_years(parsed.experience, now=date.today())
        top_skills = ", ".join(skills[:_SUMMARY_SKILLS]) or "(none listed)"
        more = f" (+{len(skills) - _SUMMARY_SKILLS} more)" if len(skills) > _SUMMARY_SKILLS else ""
        years_line = f"{years:.1f}" if years is not None else "unknown"
        return (
            f"Resume loaded for {parsed.basic_info.name or '(unnamed)'}: "
            f"{years_line} years of experience, "
            f"{len(parsed.experience)} role(s), {len(parsed.projects)} project(s), "
            f"{len(parsed.education)} education item(s). "
            f"Top skills: {top_skills}{more}."
        )

    @tool
    def retrieve_job_evidence(focus: str = "") -> str:
        """Retrieve the most relevant excerpts from the job posting as numbered
        evidence chunks, to ground suggestions in what the job actually demands.
        Optionally pass a short 'focus' phrase to steer the ranking
        (e.g. 'required skills')."""
        parsed, note = _ensure_resume_parsed(ctx)
        vectors = resume_service.get_version_embeddings(ctx.db, version_id=ctx.version.id)
        if not vectors:
            # lazy backfill（同 skill_gap_service）：舊資料可能沒向量。
            resume_service.generate_resume_embeddings(
                ctx.db, version=ctx.version, provider=ctx.provider, user_id=ctx.user.id
            )
            vectors = resume_service.get_version_embeddings(ctx.db, version_id=ctx.version.id)
        query_kind = next((k for k in _QUERY_KIND_PRIORITY if k in vectors), None)
        if query_kind is None:
            raise RuntimeError("No resume embeddings available; cannot retrieve evidence.")
        retrieved = retrieve_job_chunks(
            ctx.db, job_id=ctx.job.id, query_vector=vectors[query_kind], top_k=TOP_K
        )
        if not retrieved:
            return note + "No indexed evidence found for this job."
        # rerank query：有 focus 用 focus，否則用與 query 向量同 kind 的 embedding
        # 文字（bi-encoder 與 cross-encoder 看同一查詢，同 skill_gap_service）。
        query_text = focus.strip() or next(
            (text for kind, text in build_resume_embedding_texts(parsed) if kind == query_kind),
            "",
        )
        try:
            scores = ctx.reranker.predict(query_text, [c.content for c in retrieved])
            order = rerank_order(scores, len(retrieved))
            ranked = [retrieved[i] for i in order]
        except Exception as exc:  # rerank 任何失敗降級向量序，不中斷 run（NFR-4）
            ranked = list(retrieved)
            ctx.errors.append(f"rerank degraded to vector order: {exc}")
        ctx.retrieved_chunks = ranked
        numbered = "\n".join(
            f"[{i}] ({c.section}) {c.content[:_SUMMARY_CHUNK_CHARS]}"
            for i, c in enumerate(ranked, start=1)
        )
        return note + f"Retrieved {len(ranked)} evidence chunk(s):\n{numbered}"

    @tool
    def compute_match() -> str:
        """Compute the deterministic match score (0.0-1.0) between the resume and
        the job, with matched/missing skill breakdowns. Useful before generating,
        to know how strong the fit is and which skills are missing."""
        _, results, skipped = match_service.run_matches(
            ctx.db,
            user=ctx.user,
            resume_id=ctx.resume.id,
            job_ids=[ctx.job.id],
            provider=ctx.provider,
        )
        if not results:
            reason = skipped[0][1] if skipped else "unknown"
            raise RuntimeError(f"Match computation skipped: {reason}")
        ctx.match_result = results[0][0]
        breakdown = ctx.match_result.breakdown or {}
        matched = ", ".join((breakdown.get("matched_required") or [])[:_SUMMARY_SKILLS]) or "(none)"
        missing = ", ".join((breakdown.get("missing_required") or [])[:_SUMMARY_SKILLS]) or "(none)"
        return (
            f"Match score: {ctx.match_result.match_score:.2f}. "
            f"Matched required skills: {matched}. Missing required skills: {missing}."
        )

    @tool
    def generate_tailored_resume() -> str:
        """Generate section-level resume tailoring suggestions (bullet rewrites,
        keywords, reasons) for this job. The result is held in memory only —
        call save_artifact with kind='tailored_resume' to persist it."""
        parsed, note = _ensure_resume_parsed(ctx)
        prompt = build_tailored_resume_prompt(
            **_generation_kwargs(ctx, parsed), gap_hints=_gap_hints(ctx)
        )
        data, error = generate_tailored_resume_payload(
            ctx.db, prompt=prompt, provider=ctx.provider, user_id=ctx.user.id
        )
        if data is None:
            ctx.errors.append(f"generate_tailored_resume failed: {error}")
            return note + f"[generation_failed] tailored_resume: {error}"
        ctx.payloads["tailored_resume"] = data
        rewrites = sum(len(s.bullet_rewrites) for s in data.section_suggestions)
        return note + (
            f"Generated tailored resume suggestions: {len(data.section_suggestions)} "
            f"section(s), {rewrites} bullet rewrite(s), "
            f"{len(data.top_keywords)} top keyword(s). "
            "Call save_artifact with kind='tailored_resume' to persist."
        )

    @tool
    def generate_cover_letter() -> str:
        """Generate a tailored cover letter draft (intro, body paragraphs, closing)
        for this job. The result is held in memory only — call save_artifact with
        kind='cover_letter' to persist it."""
        parsed, note = _ensure_resume_parsed(ctx)
        prompt = build_cover_letter_prompt(
            **_generation_kwargs(ctx, parsed),
            matched_skills=_breakdown_skills(ctx, "matched_required", "matched_preferred"),
        )
        data, error = generate_cover_letter_payload(
            ctx.db, prompt=prompt, provider=ctx.provider, user_id=ctx.user.id
        )
        if data is None:
            ctx.errors.append(f"generate_cover_letter failed: {error}")
            return note + f"[generation_failed] cover_letter: {error}"
        ctx.payloads["cover_letter"] = data
        return note + (
            f"Generated cover letter draft (intro, {len(data.body_paragraphs)} body "
            "paragraph(s), closing). "
            "Call save_artifact with kind='cover_letter' to persist."
        )

    @tool
    def generate_interview_qs() -> str:
        """Generate interview preparation questions (technical, behavioral,
        project-based, skill-gap-focused) with answer outlines for this job. The
        result is held in memory only — call save_artifact with
        kind='interview_prep' to persist it."""
        parsed, note = _ensure_resume_parsed(ctx)
        prompt = build_interview_qs_prompt(
            **_generation_kwargs(ctx, parsed), gap_hints=_gap_hints(ctx)
        )
        data, error = generate_interview_prep_payload(
            ctx.db, prompt=prompt, provider=ctx.provider, user_id=ctx.user.id
        )
        if data is None:
            ctx.errors.append(f"generate_interview_qs failed: {error}")
            return note + f"[generation_failed] interview_prep: {error}"
        ctx.payloads["interview_prep"] = data
        categories = sorted({q.category for q in data.questions if q.category})
        return note + (
            f"Generated {len(data.questions)} interview question(s) covering: "
            f"{', '.join(categories) or '(uncategorized)'}. "
            "Call save_artifact with kind='interview_prep' to persist."
        )

    @tool
    def save_artifact(kind: Literal["tailored_resume", "cover_letter", "interview_prep"]) -> str:
        """Persist a previously generated artifact so the user can see it. Call
        the matching generate tool first; each kind must be saved for the run to
        be complete."""
        payload = ctx.payloads.get(kind)
        if payload is None:
            return (
                f"Nothing to save: '{kind}' has not been generated yet. "
                "Call the matching generate tool first."
            )
        # 逐 artifact commit（helper 內）：agent run 交替 LLM 記帳（自帶 commit）
        # 與寫入，無法維持單一最終 commit；每件獨立原子，run 中途失敗已保存者
        # 仍有效（NFR-4）。版號並發撞版由 helper 的 IntegrityError 重試處理。
        artifact = insert_artifact_version(
            ctx.db,
            user_id=ctx.user.id,
            resume_id=ctx.resume.id,
            resume_version_id=ctx.version.id,
            job_id=ctx.job.id,
            run_id=ctx.run_id,
            kind=kind,
            source="agent",
            content=payload.model_dump(),
        )
        ctx.saved[kind] = artifact.id
        return f"Saved {kind} as version {artifact.version_number}."

    return [
        fetch_resume,
        retrieve_job_evidence,
        compute_match,
        generate_tailored_resume,
        generate_cover_letter,
        generate_interview_qs,
        save_artifact,
    ]
