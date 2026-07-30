"""Skill gap API 的 request / response schemas（FR-24~30）。

``SkillGapPayload`` / ``SkillGapRetrieval`` 是 JSONB 欄位的 typed 鏡像：
全部有 default，向前相容舊 rows（同 MatchBreakdown 慣例）。
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class SkillGapRunRequest(BaseModel):
    """執行分析：明確指定 resume_id（同 Phase 5 決策 18，不做隱式 current resume）。"""

    resume_id: uuid.UUID


class SkillGapItemOut(BaseModel):
    """單條缺口；evidence_chunk_ids 已由 LLM 的編號映射為 chunk UUID 並通過驗證。"""

    skill: str = ""
    severity: str = "medium"  # high / medium / low（service 端已正規化）
    reason: str = ""
    evidence_chunk_ids: list[uuid.UUID] = Field(default_factory=list)
    suggestion: str = ""


class SkillGapPayload(BaseModel):
    """analysis JSONB 的 typed 鏡像。

    ``dropped_gap_count`` = citation 驗證後因零證據被丟棄的 gap 數
    （可觀測性：幻覺引用不靜默消失）。
    """

    gaps: list[SkillGapItemOut] = Field(default_factory=list)
    overall_summary: str = ""
    dropped_gap_count: int = 0


class RetrievedChunkMeta(BaseModel):
    """retrieval JSONB 內單一 chunk 的中繼資料（向量序）。"""

    chunk_id: uuid.UUID
    chunk_index: int = 0
    section: str = ""
    cosine_similarity: float = 0.0
    rerank_score: float | None = None


class SkillGapRetrieval(BaseModel):
    """retrieval JSONB 的 typed 鏡像；rerank 前後排序都保存（Phase 9 ER-5）。

    ``chunks`` 為向量（pre-rerank）序、``ranked_chunk_ids`` 為 rerank 後序；
    rerank 失敗時 ``rerank_used=False``、``ranked_chunk_ids`` 即向量序。
    """

    query_kind: str = ""
    top_k: int = 0
    chunks: list[RetrievedChunkMeta] = Field(default_factory=list)
    ranked_chunk_ids: list[uuid.UUID] = Field(default_factory=list)
    rerank_used: bool = False
    rerank_model: str | None = None
    rerank_error: str | None = None


class SkillGapChunk(BaseModel):
    """被引用 chunk 的原文（前端展開 citation 用，免二次請求）。"""

    id: uuid.UUID
    section: str
    content: str


class SkillGapReportResponse(BaseModel):
    """POST 與兩個 GET 共用；chunks 按 rerank 後順序、含全部檢索到的塊。"""

    id: uuid.UUID
    resume_id: uuid.UUID
    resume_version_number: int
    job_id: uuid.UUID
    retrieval: SkillGapRetrieval
    analysis: SkillGapPayload | None = None
    generation_error: str | None = None
    chunks: list[SkillGapChunk] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime  # = 上次執行時間（重跑覆蓋更新）
