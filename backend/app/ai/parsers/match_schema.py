"""Match 相關 structured output schemas（FR-20 語意等價、FR-22 解釋）。

供 Gemini ``generate_structured`` 作為 response_schema 使用。

設計重點（同 resume_schema.py / job_schema.py）：
- 所有欄位皆有預設值（容錯）：LLM 漏欄時驗證仍能補預設值而不整包失敗。
- ``Field(description=...)`` 會送進 Gemini schema 引導模型；default 等鍵由
  ``to_gemini_schema`` 於送出前清掉。
- 等價 pairs 與解釋內容在 service 層另有程式端驗證（``apply_equivalences``、
  只餵結構化事實），schema 本身不是唯一防線。
"""

from pydantic import BaseModel, Field


class SkillEquivalencePair(BaseModel):
    job_skill: str = Field(
        default="", description="A skill taken verbatim from the provided job skills list."
    )
    resume_skill: str = Field(
        default="",
        description="The skill taken verbatim from the provided resume skills list that "
        "refers to the same technology or ability.",
    )


class SkillEquivalenceResult(BaseModel):
    equivalences: list[SkillEquivalencePair] = Field(
        default_factory=list,
        description="Only pairs that clearly denote the SAME technology or ability "
        "under a different name. Empty list when there are none.",
    )


class MatchExplanation(BaseModel):
    why_matched: str = Field(
        default="",
        description="2-3 sentences explaining why this resume fits (or does not fit) the "
        "job, based strictly on the provided scores and skill lists.",
    )
    top_overlap: list[str] = Field(
        default_factory=list,
        description="Up to 5 of the most important overlapping skills, chosen ONLY from "
        "the provided matched skills.",
    )
    missing_skills: list[str] = Field(
        default_factory=list,
        description="The most important missing skills, chosen ONLY from the provided "
        "missing required (and notable missing preferred) skills.",
    )
    risks: list[str] = Field(
        default_factory=list,
        description="1-3 concrete weaknesses or risks for this application, e.g. an "
        "experience shortfall, each grounded in the provided facts.",
    )
