"""Job API 的 request / response schemas。"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.ai.parsers.job_schema import JobParsed


class JobCreate(BaseModel):
    """新增職缺：貼上一段原文（最短長度由 router 的 extract_plain_text 驗證；
    max_length 擋無上限 DB 寫入，與 settings.max_text_input_chars 對齊）。"""

    raw_text: str = Field(max_length=50_000)


class JobListItem(BaseModel):
    """列表單筆（輕量：不含 parsed_data / raw_text，省傳輸）。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    company: str | None
    title: str | None
    parse_status: str
    index_status: str
    created_at: datetime


class JobResponse(BaseModel):
    """職缺主回應；parsed_data / chunk_count 由 router 組裝。"""

    id: uuid.UUID
    company: str | None
    title: str | None
    parse_status: str
    parse_error: str | None
    index_status: str
    index_error: str | None
    created_at: datetime
    raw_text: str
    chunk_count: int
    parsed_data: JobParsed | None = None
