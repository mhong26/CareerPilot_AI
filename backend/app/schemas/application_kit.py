"""Application kit API 的 request / response schemas（FR-31~44、FR-45~46）。

``ArtifactResponse.content`` 存的是 kind 對應 kit_schema 的 ``model_dump()``；
router 組裝時會先過該 schema ``model_validate`` 再 dump——JSONB 明確轉回
typed 再輸出的既有慣例，舊 rows 缺欄時由 default 補齊（向前相容）。
"""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class GenerateKitRequest(BaseModel):
    """執行 agent run。``resume_id`` 可省略、預設 current resume（規劃定案；
    與 skill-gap 的強制 resume_id 刻意不同）。"""

    resume_id: uuid.UUID | None = None


class ArtifactUpdateRequest(BaseModel):
    """使用者編輯版的完整 content；shape 依 artifact kind 於 service 端驗證。"""

    content: dict[str, Any]


class ArtifactResponse(BaseModel):
    """單一 artifact（POST / GET / PATCH 共用）。"""

    id: uuid.UUID
    kind: str  # "tailored_resume" / "cover_letter" / "interview_prep"
    source: str  # "agent" / "edit"
    version_number: int
    run_id: uuid.UUID
    resume_version_number: int
    content: dict[str, Any]
    created_at: datetime


class ApplicationKitResponse(BaseModel):
    """一組 kit：三類 artifacts 的最新版（或本次 run 的產出）。

    partial 語意（NFR-4）：缺的 kind 為 None 並列於 ``missing``；``errors``
    是 run 過程降級原因（GET 時恆空）——前端據此顯示重生成引導。
    """

    job_id: uuid.UUID
    resume_id: uuid.UUID
    match_score: float | None = None
    tailored_resume: ArtifactResponse | None = None
    cover_letter: ArtifactResponse | None = None
    interview_prep: ArtifactResponse | None = None
    missing: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
