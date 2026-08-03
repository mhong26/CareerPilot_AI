"""Application kit 提示詞（FR-31~44、FR-56~58、FR-66）。

四組內容：planner 的 system prompt（agent 行為邊界——只講目標與完成條件，
**不寫死工具順序**，FR-66）＋三個 generator 的 system + builder（產物品質）。
generator 的素材全部 optional-friendly：match / 檢索證據 / gap hints 可能因
planner 決策或工具失敗而缺席，prompt 在缺席時仍能組出合法輸出（NFR-4）。
"""

# chunking 已保證單 chunk ≤1600 字元，此為防呆截斷（異常資料不爆 token）。
_MAX_CHUNK_CHARS = 1600
# 技能清單防呆上限（同 match.py / skill_gap.py）。
_MAX_SKILLS = 80
# resume summary 截斷長度（同 match.py / skill_gap.py）。
_MAX_SUMMARY_CHARS = 600
# 每個 resume section 文字區塊的截斷長度（bullet rewrite 需要原文，給得比 summary 寬）。
_MAX_SECTION_CHARS = 2000
# resume section 區塊數量上限（experience + projects 正常不會超過）。
_MAX_SECTIONS = 8

KIT_PLANNER_SYSTEM = (
    "You are an application-kit assistant helping one candidate apply to one job. "
    "Your goal for this run: produce AND save all three artifacts — tailored_resume, "
    "cover_letter, and interview_prep — using the save_artifact tool for each.\n"
    "You have seven tools. Gather what you need before generating: fetch the resume, "
    "and compute the match to understand fit; retrieve job evidence when you need "
    "grounding for suggestions. Each generate tool prepares one artifact; it is not "
    "saved until you call save_artifact for that kind.\n"
    "If a tool fails, do not give up — continue and complete as many of the remaining "
    "artifacts as possible. Do not call tools whose work is already done. Once all "
    "three artifacts are saved, respond with a short completion summary and stop "
    "calling tools."
)

TAILORED_RESUME_SYSTEM = (
    "You are a precise resume-tailoring coach. You are given structured facts from ONE "
    "resume and context about ONE job posting (numbered evidence chunks, missing "
    "skills, gap hints when available). Produce section-level tailoring suggestions "
    "with concrete bullet rewrites.\n"
    "Base every suggestion ONLY on the provided resume facts and job context — never "
    "invent experience, employers, numbers, or tools the candidate did not list. Each "
    "rewrite must keep the candidate's real facts and include a reason tied to a "
    "specific job requirement. Suggest a keyword only where the resume genuinely "
    "supports it. When rewriting, quote the original bullet exactly in 'original'; "
    "leave 'original' empty only for genuinely new bullets.\n"
    # flash-lite 實測會偷懶只回 strategy + keywords——結構最低量必須點名。
    "Structural requirements: section_suggestions must contain 2-4 sections, and each "
    "section must contain at least one bullet_rewrite. Never return an empty "
    "section_suggestions list."
)

COVER_LETTER_SYSTEM = (
    "You are a professional cover-letter writer. You are given structured facts from "
    "ONE resume and context about ONE job posting (numbered evidence chunks, matched "
    "and missing skills when available). Draft a tailored cover letter as structured "
    "blocks: intro, body paragraphs, closing.\n"
    "Base every claim ONLY on the provided resume facts — never invent experience, "
    "employers, or numbers. Lead with the candidate's strongest evidence for this "
    "specific job; address the company and role by name when provided. Keep the tone "
    "confident and specific, avoid generic filler, and keep the whole letter under "
    "roughly 350 words.\n"
    # flash-lite 實測會把整封信塞進 intro——三個區塊的邊界必須點名。
    "Structural requirements: 'intro' is ONLY the opening paragraph (2-4 sentences); "
    "the middle of the letter goes in 'body_paragraphs' as 1-3 separate paragraphs; "
    "'closing' is ONLY the final paragraph. Never leave body_paragraphs empty and "
    "never put the whole letter into intro."
)

INTERVIEW_QS_SYSTEM = (
    "You are an experienced interviewer preparing ONE candidate for ONE specific job. "
    "You are given structured resume facts and job context (numbered evidence chunks, "
    "missing skills, gap hints when available). Produce interview questions covering "
    "ALL four categories: 'technical', 'behavioral', 'project-based', and "
    "'skill-gap-focused'.\n"
    "Ground every question in the provided job context and resume facts — never invent "
    "requirements or background. Project-based questions must reference the "
    "candidate's actual projects or roles. Skill-gap-focused questions must target the "
    "listed missing skills. Each answer outline must use only the candidate's real "
    "background, 3-5 bullet points per question."
)


def _job_line(job_title: str, job_company: str) -> str:
    """Job 抬頭行——兩個 builder 共用的空值 fallback 格式。"""
    return f"Job: {job_title or '(unknown title)'} at {job_company or '(unknown company)'}\n"


def _resume_block(
    resume_skills: list[str],
    resume_summary: str,
    resume_years: float | None,
    resume_sections: list[tuple[str, str]],
) -> str:
    """履歷事實區塊：skills / summary / 年資 + 各 section 原文（bullet rewrite 素材）。"""
    skills_line = ", ".join(resume_skills[:_MAX_SKILLS]) or "(none listed)"
    summary = resume_summary.strip()[:_MAX_SUMMARY_CHARS] or "(none)"
    years_line = f"{resume_years:.1f} years" if resume_years is not None else "unknown"
    sections = "\n".join(
        f"--- {name} ---\n{content[:_MAX_SECTION_CHARS]}"
        for name, content in resume_sections[:_MAX_SECTIONS]
    )
    sections_block = f"Resume content by section:\n{sections}\n" if sections else ""
    return (
        f"Resume skills: {skills_line}\n"
        f"Resume summary: {summary}\n"
        f"Total experience: {years_line}\n"
        f"{sections_block}"
    )


def _evidence_block(evidence_chunks: list[tuple[str, str]]) -> str:
    """編號證據區塊（``(section, content)``，rerank 後順序）；無證據時為空字串。"""
    if not evidence_chunks:
        return ""
    numbered = "\n".join(
        f"[{i}] ({section}) {content[:_MAX_CHUNK_CHARS]}"
        for i, (section, content) in enumerate(evidence_chunks, start=1)
    )
    return f"Evidence chunks from the job posting:\n{numbered}\n"


def _skills_block(label: str, skills: list[str]) -> str:
    """可選技能清單區塊（missing / matched / hints）；空清單時為空字串。"""
    joined = ", ".join(skills[:_MAX_SKILLS])
    return f"{label}: {joined}\n" if joined else ""


def build_tailored_resume_prompt(
    *,
    job_title: str,
    job_company: str,
    resume_skills: list[str],
    resume_summary: str,
    resume_years: float | None,
    resume_sections: list[tuple[str, str]],
    evidence_chunks: list[tuple[str, str]],
    missing_skills: list[str],
    gap_hints: list[str],
) -> str:
    """組客製化履歷建議的 user prompt（FR-31~35）。

    ``resume_sections`` 為 ``(section 名, 原文區塊)``——bullet rewrite 需要原句
    才能填 ``original``；``gap_hints`` 來自同 pair 的 SkillGapReport，僅作提示。
    """
    return (
        "Suggest how to tailor this resume for this job.\n\n"
        f"{_job_line(job_title, job_company)}"
        f"{_evidence_block(evidence_chunks)}"
        f"{_skills_block('Skills the job requires that the resume lacks', missing_skills)}"
        f"{_skills_block('Known skill-gap hints from a prior analysis', gap_hints)}"
        f"\n{_resume_block(resume_skills, resume_summary, resume_years, resume_sections)}"
    )


def build_cover_letter_prompt(
    *,
    job_title: str,
    job_company: str,
    resume_skills: list[str],
    resume_summary: str,
    resume_years: float | None,
    resume_sections: list[tuple[str, str]],
    evidence_chunks: list[tuple[str, str]],
    matched_skills: list[str],
    missing_skills: list[str],
) -> str:
    """組 cover letter 草稿的 user prompt（FR-37~39）。

    ``matched_skills`` 是主賣點素材；使用者偏好（語氣等）Phase 8 才注入，
    本 phase 不留參數。
    """
    return (
        "Draft a tailored cover letter for this application.\n\n"
        f"{_job_line(job_title, job_company)}"
        f"{_evidence_block(evidence_chunks)}"
        f"{_skills_block('Candidate strengths matching the job', matched_skills)}"
        f"{_skills_block('Job requirements the resume does not show (do not overclaim)', missing_skills)}"
        f"\n{_resume_block(resume_skills, resume_summary, resume_years, resume_sections)}"
    )


def build_interview_qs_prompt(
    *,
    job_title: str,
    job_company: str,
    resume_skills: list[str],
    resume_summary: str,
    resume_years: float | None,
    resume_sections: list[tuple[str, str]],
    evidence_chunks: list[tuple[str, str]],
    missing_skills: list[str],
    gap_hints: list[str],
) -> str:
    """組面試準備題組的 user prompt（FR-41~43）；gap 類題目以 missing/hints 為靶。"""
    return (
        "Prepare interview questions for this candidate applying to this job. Cover "
        "all four categories: technical, behavioral, project-based, and "
        "skill-gap-focused.\n\n"
        f"{_job_line(job_title, job_company)}"
        f"{_evidence_block(evidence_chunks)}"
        f"{_skills_block('Missing skills to probe in skill-gap-focused questions', missing_skills)}"
        f"{_skills_block('Known skill-gap hints from a prior analysis', gap_hints)}"
        f"\n{_resume_block(resume_skills, resume_summary, resume_years, resume_sections)}"
    )
