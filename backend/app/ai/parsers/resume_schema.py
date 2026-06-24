"""履歷結構化輸出 schema（FR-9）。

供 Gemini ``generate_structured`` 作為 response_schema 使用。所有欄位皆可空 /
有預設值：LLM 漏抽某段時仍能通過 validation，不致整包失敗（容錯，FR-10）。
"""

from pydantic import BaseModel, Field


class BasicInfo(BaseModel):
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    location: str | None = None
    links: list[str] = Field(default_factory=list)


class ExperienceItem(BaseModel):
    company: str | None = None
    title: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    bullets: list[str] = Field(default_factory=list)


class ProjectItem(BaseModel):
    name: str | None = None
    description: str | None = None
    tech: list[str] = Field(default_factory=list)


class EducationItem(BaseModel):
    school: str | None = None
    degree: str | None = None
    field: str | None = None
    graduation: str | None = None


class ResumeParsed(BaseModel):
    basic_info: BasicInfo = Field(default_factory=BasicInfo)
    summary: str | None = None
    skills: list[str] = Field(default_factory=list)
    experience: list[ExperienceItem] = Field(default_factory=list)
    projects: list[ProjectItem] = Field(default_factory=list)
    education: list[EducationItem] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
