"""履歷結構化輸出 schema（FR-9）。

供 Gemini ``generate_structured`` 作為 response_schema 使用。

設計重點：
- 所有欄位皆有預設值（容錯）：LLM 漏抽或履歷本身缺該資訊時，驗證仍能補預設值
  而不整包失敗（FR-10）。Gemini 不接受帶 default 的 schema，故送出前由
  ``app.ai.llm.schema_utils.to_gemini_schema`` 清掉 default 等鍵。
- 每個關鍵欄位帶 ``Field(description=...)``：這段說明會一併送進 Gemini schema
  （Gemini 支援 property description），用來引導模型把內容放對欄位——特別是
  區分「個別技能 vs 分類標籤」與「project 的 bullets（成果描述）vs tech（技術名）」。
"""

from pydantic import BaseModel, Field


class BasicInfo(BaseModel):
    name: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    links: list[str] = Field(default_factory=list, description="Profile / portfolio URLs.")


class ExperienceItem(BaseModel):
    company: str = Field(default="", description="Employer / company name only.")
    title: str = Field(
        default="",
        description=(
            "Job title / role held. It often sits on the same line as the company and "
            "dates — e.g. in 'Jan 2026 - May 2026  Appy.yo  Software Engineer' the title "
            "is 'Software Engineer'. Extract just the role, not the company or dates."
        ),
    )
    start_date: str = Field(
        default="",
        description=(
            "Start date ONLY (e.g. 'Jan 2026'). Do NOT include the end date, a date "
            "range, the company, or the title here."
        ),
    )
    end_date: str = Field(default="", description="End date ONLY (e.g. 'May 2026' or 'Present').")
    bullets: list[str] = Field(
        default_factory=list,
        description=(
            "Each responsibility / achievement line for this role, copied verbatim. "
            "Put ALL such lines here — do not merge them into the date or title fields."
        ),
    )


class ProjectItem(BaseModel):
    name: str = Field(default="", description="Project name or title.")
    description: str = Field(
        default="",
        description="Optional one-line summary of the project. Leave empty if there is none.",
    )
    bullets: list[str] = Field(
        default_factory=list,
        description=(
            "Every descriptive or achievement line about this project, copied verbatim. "
            "Put ALL such lines here — never drop them and never move them into 'tech'."
        ),
    )
    tech: list[str] = Field(
        default_factory=list,
        description=(
            "Only the technology / tool / library names used (e.g. Python, React, AWS). "
            "Do NOT put achievement sentences here."
        ),
    )


class EducationItem(BaseModel):
    school: str = ""
    degree: str = ""
    field: str = ""
    graduation: str = ""


class ResumeParsed(BaseModel):
    basic_info: BasicInfo = Field(default_factory=BasicInfo)
    summary: str = ""
    skills: list[str] = Field(
        default_factory=list,
        description=(
            "Individual skills, one per item (e.g. 'Python', 'React', 'AWS'). "
            "Split comma-separated or category-grouped skill lists into separate items "
            "and drop any category label (e.g. 'Programming Languages:')."
        ),
    )
    experience: list[ExperienceItem] = Field(default_factory=list)
    projects: list[ProjectItem] = Field(default_factory=list)
    education: list[EducationItem] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
