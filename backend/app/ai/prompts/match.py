"""Match 提示詞：技能語意等價（FR-20）與 match explanation（FR-22）。

兩個任務都刻意「不讓 LLM 自己評估」：等價判斷只在正規化比對失敗的技能間
配對；explanation 只把算好的結構化事實轉述成人話。兩者的輸出都另有程式端
驗證 / 落地（``apply_equivalences``、explanation 存 JSONB）。
"""

# 技能清單防呆上限：正常職缺遠小於此；防異常輸入爆 token。
_MAX_SKILLS_PER_LIST = 80
# resume summary 進 explanation 的截斷長度。
_MAX_SUMMARY_CHARS = 600

SKILL_EQUIVALENCE_SYSTEM = (
    "You are a precise technical-skill matcher. You are given two lists: job skills "
    "and resume skills. Identify pairs that clearly refer to the SAME technology or "
    "ability under a different name or spelling — e.g. 'Go' and 'Golang', 'Postgres' "
    "and 'PostgreSQL', 'React' and 'React.js', 'k8s' and 'Kubernetes'.\n"
    "Do NOT pair merely related things: 'Java' is NOT 'JavaScript'; a framework is "
    "NOT its language (Django is not Python); a cloud provider is NOT a service on "
    "it. When unsure, leave the pair out. Return an empty list if nothing matches. "
    "Use the exact strings from the lists — never invent new skill names."
)


def build_skill_equivalence_prompt(job_skills: list[str], resume_skills: list[str]) -> str:
    """把兩份（截斷後的）技能清單組成 user prompt。"""
    job_part = ", ".join(job_skills[:_MAX_SKILLS_PER_LIST])
    resume_part = ", ".join(resume_skills[:_MAX_SKILLS_PER_LIST])
    return (
        "Find equivalent skill pairs between these two lists.\n\n"
        f"Job skills: {job_part}\n"
        f"Resume skills: {resume_part}"
    )


MATCH_EXPLANATION_SYSTEM = (
    "You are a precise career-match analyst. You are given pre-computed matching "
    "facts between a resume and a job: component scores, matched and missing skill "
    "lists, and experience data. Write a concise explanation for the job seeker.\n"
    "Base every statement ONLY on the provided facts — never invent skills, "
    "experience, or numbers that are not present. Pick 'top_overlap' only from the "
    "matched skills and 'missing_skills' only from the missing skills. Keep the tone "
    "factual and constructive."
)


def build_match_explanation_prompt(
    *,
    job_title: str,
    job_company: str,
    match_score: float,
    breakdown: dict,
    resume_summary: str,
) -> str:
    """把結構化匹配事實組成 user prompt（LLM 只轉述、不重新評估）。"""

    def _pct(value: float | None) -> str:
        return f"{value:.0%}" if value is not None else "n/a"

    def _items(values: list[str]) -> str:
        return ", ".join(values) if values else "(none)"

    pairs = breakdown.get("equivalent_pairs") or []
    pairs_line = (
        "; ".join(f"{p['job_skill']} ≈ {p['resume_skill']}" for p in pairs) if pairs else "(none)"
    )
    resume_years = breakdown.get("resume_years")
    required_years = breakdown.get("required_years")
    years_line = (
        f"{resume_years:.1f} years vs required {required_years:.1f} years"
        if resume_years is not None and required_years is not None
        else "n/a"
    )
    summary = resume_summary.strip()[:_MAX_SUMMARY_CHARS] or "(none)"

    return (
        "Explain this resume-job match based on the facts below.\n\n"
        f"Job: {job_title or '(unknown title)'} at {job_company or '(unknown company)'}\n"
        f"Overall match score: {match_score:.0%}\n"
        f"Component scores: embedding similarity {_pct(breakdown.get('embedding_similarity'))}, "
        f"required skill coverage {_pct(breakdown.get('required_coverage'))}, "
        f"preferred skill coverage {_pct(breakdown.get('preferred_coverage'))}, "
        f"experience alignment {_pct(breakdown.get('experience_alignment'))}\n"
        f"Matched required skills: {_items(breakdown.get('matched_required', []))}\n"
        f"Missing required skills: {_items(breakdown.get('missing_required', []))}\n"
        f"Matched preferred skills: {_items(breakdown.get('matched_preferred', []))}\n"
        f"Missing preferred skills: {_items(breakdown.get('missing_preferred', []))}\n"
        f"Equivalent skills counted as matched: {pairs_line}\n"
        f"Experience: {years_line}\n"
        f"Resume summary: {summary}"
    )
