"""Skill gap 提示詞（FR-27 生成、FR-28 引用、FR-29 嚴重度、FR-30 建議）。

證據以「編號 chunk」呈現、履歷以結構化事實呈現；LLM 只能在兩者交集上作
結論，每條 gap 必須引用 chunk 編號。程式端另有 citation 驗證
（``skill_gap_service.validate_analysis``），schema 與 prompt 都不是唯一防線。
"""

# chunking 已保證單 chunk ≤1600 字元，此為防呆截斷（異常資料不爆 token）。
_MAX_CHUNK_CHARS = 1600
# 技能清單防呆上限（同 match.py）。
_MAX_SKILLS = 80
# resume summary 截斷長度（同 match.py）。
_MAX_SUMMARY_CHARS = 600

SKILL_GAP_SYSTEM = (
    "You are a precise career-gap analyst. You are given numbered evidence chunks from "
    "ONE job posting and structured facts from ONE resume. Identify the skills and "
    "qualifications the job demands that the resume does not demonstrate.\n"
    "Base every statement ONLY on the provided evidence chunks and resume facts — never "
    "invent job requirements, resume skills, or numbers. Never list a skill the resume "
    "already contains as a gap. Every gap MUST cite at least one evidence chunk by its "
    "number, using only numbers from the provided list. Severity rules: 'high' when the "
    "cited evidence marks the skill as required or core; 'medium' when the resume shows "
    "a related or partial skill; 'low' when the evidence marks it preferred or "
    "nice-to-have. Each suggestion must be one concrete, actionable step. Return an "
    "empty gaps list if there are no real gaps."
)


def build_skill_gap_prompt(
    *,
    job_title: str,
    job_company: str,
    chunks: list[tuple[str, str]],
    resume_skills: list[str],
    resume_summary: str,
    resume_years: float | None,
    prior_missing_skills: list[str],
) -> str:
    """把編號證據（rerank 後順序的 ``(section, content)``）與履歷事實組成 user prompt。

    ``prior_missing_skills`` 來自同履歷版本的 MatchResult breakdown，僅作提示
    ——prompt 明令逐條對照證據後才能採用，不得直接照抄。
    """
    numbered = "\n".join(
        f"[{i}] ({section}) {content[:_MAX_CHUNK_CHARS]}"
        for i, (section, content) in enumerate(chunks, start=1)
    )
    skills_line = ", ".join(resume_skills[:_MAX_SKILLS]) or "(none listed)"
    summary = resume_summary.strip()[:_MAX_SUMMARY_CHARS] or "(none)"
    years_line = f"{resume_years:.1f} years" if resume_years is not None else "unknown"
    hints = ", ".join(prior_missing_skills[:_MAX_SKILLS])
    hint_block = (
        "\nSkills a previous deterministic match marked as missing (hints only — verify "
        f"each against the evidence chunks before including): {hints}\n"
        if hints
        else ""
    )
    return (
        "Analyze the skill gaps between this resume and job.\n\n"
        f"Job: {job_title or '(unknown title)'} at {job_company or '(unknown company)'}\n"
        f"Evidence chunks from the job posting (cite by number):\n{numbered}\n\n"
        f"Resume skills: {skills_line}\n"
        f"Resume summary: {summary}\n"
        f"Total experience: {years_line}\n"
        f"{hint_block}"
    )
