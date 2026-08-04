"""No-RAG skill-gap baseline（ER-6 對照組）：不給檢索證據的直接生成。

與正式 RAG 流程的唯一差異：**看不到職缺內文**——只有職稱 / 公司與履歷事實。
模型要「猜」職缺要求什麼，幻覺（宣稱職缺要求了它沒要求的東西）預期上升；
judge 端反而給它最寬容的證據集（該 job 全部 chunk 原文）——偏向**低估**
RAG 的改善幅度，量到的差距因此更可信（報告記載此設計）。

System prompt 刻意鏡射 ``SKILL_GAP_SYSTEM`` 的角色與 severity 規則，僅移除
證據引用要求——隔離變因就是「有沒有 RAG」。
"""

from app.ai.llm.base import LLMError
from app.services.match_scoring import resume_skill_pool
from pydantic import BaseModel, Field

import eval._bootstrap  # noqa: F401  # isort: split
from eval.datasets.schema import Scenario
from eval.harness import CachingProvider

_MAX_SKILLS = 80
_MAX_SUMMARY_CHARS = 600

NO_RAG_SYSTEM = (
    "You are a precise career-gap analyst. You are given a job title and company, and "
    "structured facts from ONE resume. Identify the skills and qualifications this job "
    "most likely demands that the resume does not demonstrate.\n"
    "Never list a skill the resume already contains as a gap. Severity rules: 'high' "
    "for skills that would be required or core for such a role; 'medium' when the "
    "resume shows a related or partial skill; 'low' for nice-to-have skills. Each "
    "suggestion must be one concrete, actionable step. Return an empty gaps list if "
    "there are no real gaps."
)


class BaselineGapItem(BaseModel):
    skill: str = Field(default="", description="The missing or weak skill.")
    severity: str = Field(default="medium", description="'high', 'medium', or 'low'.")
    reason: str = Field(default="", description="One sentence explaining the gap.")
    suggestion: str = Field(default="", description="One concrete improvement step.")


class BaselineGapAnalysis(BaseModel):
    gaps: list[BaselineGapItem] = Field(default_factory=list)
    overall_summary: str = Field(default="")


def build_no_rag_prompt(scenario: Scenario) -> str:
    gap_job = next(j for j in scenario.jobs if j.job_key == scenario.skill_gap.job_key)
    parsed = scenario.resume
    skills_line = ", ".join(resume_skill_pool(parsed)[:_MAX_SKILLS]) or "(none listed)"
    summary = parsed.summary.strip()[:_MAX_SUMMARY_CHARS] or "(none)"
    return (
        "Analyze the skill gaps between this resume and job.\n\n"
        f"Job: {gap_job.parsed.title or '(unknown title)'} at "
        f"{gap_job.parsed.company or '(unknown company)'}\n"
        "(No job description text is available — infer typical demands of this role.)\n\n"
        f"Resume skills: {skills_line}\n"
        f"Resume summary: {summary}\n"
    )


def generate_baseline_gaps(
    provider: CachingProvider, scenario: Scenario
) -> tuple[BaselineGapAnalysis | None, str | None]:
    """產生 no-RAG baseline 分析；失敗回 ``(None, error)``（不記 LLMCallLog——
    baseline 不是 production 流量，不應汙染 reliability 統計）。"""
    try:
        result = provider.generate_structured(
            build_no_rag_prompt(scenario), BaselineGapAnalysis, system=NO_RAG_SYSTEM
        )
    except LLMError as exc:
        return None, str(exc)
    return result.data, None
