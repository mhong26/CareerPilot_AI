"""Application kit structured output schemas（FR-31~36 履歷建議、FR-37~40 cover letter、FR-41~44 面試題）。

供 Gemini ``generate_structured`` 作為 response_schema 使用。

設計重點（同 skill_gap_schema.py）：
- 所有欄位皆有預設值（容錯）：LLM 漏欄時驗證仍能補預設值而不整包失敗，
  部分輸出照樣入庫（NFR-4）。
- ``Field(description=...)`` 會送進 Gemini schema 引導模型；值域（section、
  category）直接寫在 description 裡，不用 Enum——與全 repo「字串 + 註解」慣例一致。
- 三個 schema 對應 GeneratedArtifact.kind 三值，``model_dump()`` 後存 JSONB，
  前端依 kind 分區塊渲染與編輯，Phase 9 rubric evaluator 逐欄位評分。
"""

from pydantic import BaseModel, Field


class BulletRewrite(BaseModel):
    original: str = Field(
        default="",
        description="The exact bullet or sentence from the resume being improved. "
        "Empty string when suggesting a brand-new bullet to add.",
    )
    improved: str = Field(
        default="",
        description="The rewritten bullet, ready to paste into the resume. Keep the "
        "candidate's real facts — sharpen wording and alignment, never invent "
        "experience, numbers, or tools.",
    )
    reason: str = Field(
        default="",
        description="One sentence explaining why this rewrite helps, tied to a specific "
        "job requirement or skill gap from the provided evidence.",
    )


class SectionSuggestion(BaseModel):
    section: str = Field(
        default="",
        description="Resume section this suggestion targets: 'summary', 'experience', "
        "'projects', or 'skills'.",
    )
    bullet_rewrites: list[BulletRewrite] = Field(
        default_factory=list,
        description="Concrete rewrites for this section. Prefer rewriting existing "
        "bullets over inventing new ones.",
    )
    keywords_to_add: list[str] = Field(
        default_factory=list,
        description="Keywords from the job posting worth adding to this section, only "
        "where the resume genuinely supports them.",
    )
    note: str = Field(
        default="",
        description="Optional short note about this section (e.g. reordering advice). "
        "Empty when there is nothing extra to say.",
    )


class TailoredResumeSuggestions(BaseModel):
    overall_strategy: str = Field(
        default="",
        description="2-3 sentence positioning strategy for tailoring this resume to "
        "this specific job.",
    )
    section_suggestions: list[SectionSuggestion] = Field(
        default_factory=list,
        description="Per-section suggestions. Include only sections that need changes.",
    )
    top_keywords: list[str] = Field(
        default_factory=list,
        description="The most important keywords from the job posting to surface across "
        "the resume, ordered by importance.",
    )


class CoverLetterDraft(BaseModel):
    intro: str = Field(
        default="",
        description="Opening paragraph: the role applied for and a hook connecting the "
        "candidate's strongest relevant qualification to the company's need.",
    )
    body_paragraphs: list[str] = Field(
        default_factory=list,
        description="1-3 body paragraphs, each matching concrete resume evidence to "
        "specific job requirements. Never invent experience.",
    )
    closing: str = Field(
        default="",
        description="Closing paragraph: enthusiasm, fit summary, and a call to action.",
    )


class InterviewQuestion(BaseModel):
    question: str = Field(
        default="",
        description="The interview question, phrased as an interviewer would ask it.",
    )
    category: str = Field(
        default="",
        description="Question type: 'technical', 'behavioral', 'project-based', or "
        "'skill-gap-focused'.",
    )
    why_it_matters: str = Field(
        default="",
        description="One sentence on why this employer would ask this, grounded in the "
        "job posting.",
    )
    related_resume_area: str = Field(
        default="",
        description="Which part of the candidate's resume this question probes (e.g. a "
        "specific project, role, or listed skill). Empty for pure gap questions.",
    )
    answer_outline: list[str] = Field(
        default_factory=list,
        description="3-5 bullet points outlining a strong answer using the candidate's "
        "actual background.",
    )


class InterviewPrepSet(BaseModel):
    questions: list[InterviewQuestion] = Field(
        default_factory=list,
        description="Interview questions covering all four categories: technical, "
        "behavioral, project-based, and skill-gap-focused.",
    )
