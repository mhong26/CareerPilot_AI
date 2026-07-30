"""SkillGapReport model — RAG 技能缺口報告（FR-24~30）。

每組 (resume, job) 只保留最新一筆（unique constraint + service 層 upsert），
``updated_at`` 即「上次執行時間」。``resume_version_id`` 記錄分析當下的履歷
快照。``retrieval`` 保存 rerank 前後兩份排序與分數（Phase 9 ER-5 對比的
資料來源）；``analysis`` 為 LLM 產物、允許失敗（NFR-4 降級對）。
"""

import uuid
from typing import Any

from sqlalchemy import ForeignKey, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import TimestampMixin, UUIDPKMixin
from app.db.session import Base


class SkillGapReport(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "skill_gap_reports"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    # 索引由 unique constraint 的前導欄位提供，不另建（同 MatchResult.resume_id 慣例）。
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

    # 檢索中繼資料（SkillGapRetrieval 形狀）：query_kind、向量序 chunks（含
    # cosine 與 rerank 分數）、rerank 後的 ranked_chunk_ids、rerank_used /
    # rerank_model / rerank_error。確定性結果，生成失敗也照存。
    retrieval: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    # 通過 citation 驗證的分析（SkillGapPayload 形狀：gaps / overall_summary /
    # dropped_gap_count）；LLM 生成失敗為 None，原因入 generation_error。
    analysis: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    generation_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("resume_id", "job_id", name="uq_skill_gap_reports_resume_job"),
    )
