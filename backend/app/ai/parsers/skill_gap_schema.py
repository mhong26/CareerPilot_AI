"""Skill gap structured output schema（FR-27 生成、FR-28 引用、FR-29 嚴重度、FR-30 建議）。

供 Gemini ``generate_structured`` 作為 response_schema 使用。

設計重點（同 match_schema.py）：
- 所有欄位皆有預設值（容錯）：LLM 漏欄時驗證仍能補預設值而不整包失敗。
- ``Field(description=...)`` 會送進 Gemini schema 引導模型。
- 引用用 **1-based chunk 編號**而非 UUID：36 字元 UUID 要求 LLM 回抄容易
  幻覺且浪費 token；整數 1..k 驗證簡單，service 層
  （``skill_gap_service.validate_analysis``）再映射回 chunk UUID——schema
  不是唯一防線。
"""

from pydantic import BaseModel, Field


class SkillGapItem(BaseModel):
    skill: str = Field(
        default="",
        description="The missing or weak skill, named as it appears in the job evidence.",
    )
    severity: str = Field(
        default="medium",
        description="'high' when the cited evidence marks the skill as required or core; "
        "'medium' when the resume shows only a related or partial skill; 'low' when the "
        "evidence marks it preferred or nice-to-have.",
    )
    reason: str = Field(
        default="",
        description="One sentence explaining the gap, grounded strictly in the cited "
        "evidence chunks and the provided resume facts.",
    )
    evidence_chunk_numbers: list[int] = Field(
        default_factory=list,
        description="Numbers of the evidence chunks (from the numbered list) that demand "
        "this skill. Every gap MUST cite at least one number from the provided list.",
    )
    suggestion: str = Field(
        default="",
        description="One concrete, actionable improvement suggestion for closing this gap.",
    )


class SkillGapAnalysis(BaseModel):
    gaps: list[SkillGapItem] = Field(
        default_factory=list,
        description="Skill gaps found. Empty list when the resume already covers "
        "everything the evidence demands.",
    )
    overall_summary: str = Field(
        default="",
        description="2-3 sentence overall readiness assessment based only on the "
        "provided evidence and resume facts.",
    )
