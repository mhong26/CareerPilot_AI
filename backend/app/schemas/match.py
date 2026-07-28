"""Match API 的 request / response schemas（FR-19~23）。"""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.ai.parsers.match_schema import MatchExplanation


class MatchRunRequest(BaseModel):
    """執行匹配：一份履歷 × 一批職缺（FR-19）。

    上限 50：run 為同步計算（每 job 最多 2 次 LLM 呼叫），必須有界。
    """

    resume_id: uuid.UUID
    job_ids: list[uuid.UUID] = Field(min_length=1, max_length=50)


class EquivalentPair(BaseModel):
    """LLM 判定並通過程式端驗證的等價技能對（如 Go ≈ Golang）。"""

    job_skill: str
    resume_skill: str


class MatchBreakdown(BaseModel):
    """breakdown JSONB 的 typed 鏡像；全部有 default，向前相容舊 rows。

    成分為 None 代表該成分不可用（權重已歸一化，見 weights_used），
    而非 0 分。
    """

    embedding_similarity: float | None = None
    required_coverage: float | None = None
    preferred_coverage: float | None = None
    experience_alignment: float | None = None
    matched_required: list[str] = Field(default_factory=list)
    missing_required: list[str] = Field(default_factory=list)
    matched_preferred: list[str] = Field(default_factory=list)
    missing_preferred: list[str] = Field(default_factory=list)
    equivalent_pairs: list[EquivalentPair] = Field(default_factory=list)
    llm_equivalence_used: bool = False
    resume_years: float | None = None
    required_years: float | None = None
    years_score: float | None = None
    title_similarity: float | None = None
    weights_used: dict[str, float] = Field(default_factory=dict)


class MatchResultItem(BaseModel):
    """單筆匹配結果；run 與 GET /matches 共用。job 欄位反正規化供列表顯示。"""

    id: uuid.UUID
    job_id: uuid.UUID
    job_title: str | None
    job_company: str | None
    match_score: float
    breakdown: MatchBreakdown
    explanation: MatchExplanation | None = None
    explanation_error: str | None = None
    created_at: datetime
    updated_at: datetime  # = 上次執行時間（重跑覆蓋更新）


class MatchSkipped(BaseModel):
    """未能計分的 job 與原因："not_found"（不存在 / 非本人）/ "not_parsed"。"""

    job_id: uuid.UUID
    reason: str


class MatchRunResponse(BaseModel):
    """POST /matches/run 回應；results 按 match_score desc（FR-21）。"""

    resume_id: uuid.UUID
    resume_version_number: int
    results: list[MatchResultItem]
    skipped: list[MatchSkipped]
