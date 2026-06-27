"""Resume API 的 request / response schemas。"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.ai.parsers.resume_schema import ResumeParsed


class ResumeVersionResponse(BaseModel):
    """版本列表的單筆（不含完整 parsed_data，省傳輸）。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    version_number: int
    label: str
    created_at: datetime


class ResumeResponse(BaseModel):
    """履歷主回應；parsed_data / current_version_number 由 router 取自目前版本組裝。"""

    id: uuid.UUID
    source_filename: str | None
    source_type: str
    parse_status: str
    parse_error: str | None
    created_at: datetime
    current_version_number: int | None = None
    parsed_data: ResumeParsed | None = None


class ResumeUpdate(BaseModel):
    """PATCH 編輯：送整份結構化履歷 → 存成新版本。"""

    parsed_data: ResumeParsed
