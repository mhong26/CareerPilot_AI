"""履歷結構化輸出 schema（FR-9）。

供 Gemini ``generate_structured`` 作為 response_schema 使用。

重要：Gemini 的 response_schema **不接受帶 ``default`` 的欄位**（Pydantic 的預設值會
被序列化成 ``default`` 鍵，導致 "Unknown field for Schema: default"）。因此這裡所有
欄位皆為**必填且非 nullable**——缺項由模型填空字串 / 空陣列（見 prompt 指示），
如此 Gemini 必定回傳每個欄位、驗證穩定通過。
"""

from pydantic import BaseModel


class BasicInfo(BaseModel):
    name: str
    email: str
    phone: str
    location: str
    links: list[str]


class ExperienceItem(BaseModel):
    company: str
    title: str
    start_date: str
    end_date: str
    bullets: list[str]


class ProjectItem(BaseModel):
    name: str
    description: str
    tech: list[str]


class EducationItem(BaseModel):
    school: str
    degree: str
    field: str
    graduation: str


class ResumeParsed(BaseModel):
    basic_info: BasicInfo
    summary: str
    skills: list[str]
    experience: list[ExperienceItem]
    projects: list[ProjectItem]
    education: list[EducationItem]
    certifications: list[str]
