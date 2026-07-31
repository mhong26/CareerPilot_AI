"""GeneratedArtifact model — Application Kit 產物（FR-31~44、FR-54~55）。

三類產物（tailored_resume / cover_letter / interview_prep）採 **append-only**：
每次生成或編輯都插入新 row，永不 UPDATE 內容、永不 DELETE——歷史本身有價值
（FR-54、SRS §5.3.4）。「最新版」= 同 ``(resume_id, job_id, kind)`` 下
``version_number`` 最大者，因此不設 unique constraint（與 MatchResult /
SkillGapReport 的「每對唯一、覆寫升級」刻意不同：那兩者是分析快照，這裡是
創作產物）。``run_id`` 讓同一次 agent run 產出的三個 artifacts 可被歸組；
使用者編輯版沿用原 run_id、``source="edit"``。
"""

import uuid
from typing import Any

from sqlalchemy import ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import TimestampMixin, UUIDPKMixin
from app.db.session import Base


class GeneratedArtifact(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "generated_artifacts"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    # 索引由 composite index 的前導欄位提供，不另建（同 SkillGapReport.resume_id 慣例）。
    resume_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("resumes.id", ondelete="CASCADE"),
        nullable=False,
    )
    # 生成當下的履歷版本快照（編輯出新 resume version 不影響既有 artifacts）。
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
    # 同一次 agent run 的三個 artifacts 共用（服務層產生，非 FK）；編輯版沿用原值。
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        index=True,
        nullable=False,
    )
    # "tailored_resume" / "cover_letter" / "interview_prep"（FR-57 三類產物）。
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    # "agent"（agent 產出）/ "edit"（使用者編輯版，FR-40）。
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    # 同 (resume_id, job_id, kind) 遞增，1 起算；最新版 = 最大者。
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    # 對應 kind 的 structured payload（kit_schema 各 schema 的 model_dump()）。
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        # 「查某 pair 某 kind 的最新版」的主要查詢路徑。
        Index("ix_generated_artifacts_pair_kind", "resume_id", "job_id", "kind"),
    )
