"""職缺結構化輸出 schema（FR-14）。

供 Gemini ``generate_structured`` 作為 response_schema 使用（同履歷解析）。

設計重點（同 resume_schema.py）：
- 所有欄位皆有預設值：LLM 漏抽或職缺本身缺該資訊時，驗證仍能補預設值而不整包失敗
  （FR-10）。Gemini 不接受帶 default 的 schema，故送出前由 ``to_gemini_schema`` 清掉。
- 每個 list 欄位帶 ``Field(description=...)``：說明會一併送進 Gemini，引導模型把內容
  放對欄位——尤其是「一項技能一個項目」與「required vs preferred」的區分。
"""

from pydantic import BaseModel, Field


class JobParsed(BaseModel):
    company: str = Field(default="", description="Hiring company / organization name only.")
    title: str = Field(
        default="",
        description="Job title of the position, e.g. 'Senior Backend Engineer'. Just the role name.",
    )
    location: str = Field(
        default="", description="Work location (city / country) if stated; otherwise empty."
    )
    work_mode: str = Field(
        default="",
        description="'remote', 'hybrid', or 'onsite' if stated; otherwise empty.",
    )
    responsibilities: list[str] = Field(
        default_factory=list,
        description=(
            "Each responsibility / duty line as its own item, copied verbatim. "
            "Never merge several lines into one item."
        ),
    )
    required_skills: list[str] = Field(
        default_factory=list,
        description=(
            "Individual must-have skills, one per item (e.g. 'Python'). Split "
            "comma-separated lists into separate items and drop any category label. "
            "A skill is required unless the text explicitly marks it preferred / a plus."
        ),
    )
    preferred_skills: list[str] = Field(
        default_factory=list,
        description=(
            "Individual nice-to-have skills, one per item. Only skills the text explicitly "
            "marks as preferred / nice-to-have / a plus / bonus."
        ),
    )
    qualifications: list[str] = Field(
        default_factory=list,
        description="Education, certification and other qualification lines, one per item.",
    )
    experience_requirements: list[str] = Field(
        default_factory=list,
        description=(
            "Experience requirement lines (e.g. '5+ years of backend development'), "
            "one per item."
        ),
    )
