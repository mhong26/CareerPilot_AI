"""MatchResult model — 履歷×職缺的匹配分數、成分明細與解釋（FR-20~23）。

每組 (resume, job) 只保留最新一筆（unique constraint + service 層 upsert），
``updated_at`` 即「上次執行時間」。``resume_version_id`` 記錄評分當下的履歷
快照，供稽核與 Phase 9 evaluation 重現。
"""

import uuid
from typing import Any

from sqlalchemy import Float, ForeignKey, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import TimestampMixin, UUIDPKMixin
from app.db.session import Base


class MatchResult(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "match_results"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    # 索引由 unique constraint 的前導欄位提供，不另建（同 JobEmbedding.chunk_id 慣例）。
    resume_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("resumes.id", ondelete="CASCADE"),
        nullable=False,
    )
    resume_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("resume_versions.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    # 加權合成分數 [0,1]；Phase 7 agent 以 0.5 / 0.8 門檻對此欄位路由，
    # 必須是 top-level float 而非藏在 JSONB 內。
    match_score: Mapped[float] = mapped_column(Float, nullable=False)

    # 各成分分數、matched/missing 技能清單、實際使用權重等（MatchBreakdown 形狀）。
    breakdown: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    # LLM 生成的結構化解釋（MatchExplanation.model_dump()）；生成失敗為 None，
    # 分數照存（NFR-4），失敗原因入 explanation_error。
    explanation: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    explanation_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (UniqueConstraint("resume_id", "job_id", name="uq_match_results_resume_job"),)
