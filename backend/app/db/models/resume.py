"""Resume / ResumeVersion / ResumeEmbedding models — 履歷原文、版本歷史與向量。

Resume 存「一次上傳的事實」：原文、來源型別、解析狀態。
ResumeVersion 存「結構化資料的歷史快照」：初次解析為 v1，每次編輯 +1（FR-12）。
目前生效版本 = 該 resume 中 version_number 最大者（不另設指標欄，避免循環 FK）。
ResumeEmbedding 存每個版本的 section 向量（summary / skills / experience），
是 Phase 5 match ranking embedding similarity 的查詢側資料（FR-20）。
"""

import uuid
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models.base import TimestampMixin, UUIDPKMixin
from app.db.session import Base

# 向量維度寫死（不讀 settings.embedding_dim）：create_all（本地測試）與 migration（CI）
# 產出的 DDL 必須一致、不受環境變數影響（同 job.py 的理由）。
_VECTOR_DIM = 768


class Resume(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "resumes"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    # 原始檔名（貼上文字時可為空）。
    source_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # "pdf" / "docx" / "text" —— 來源型別，方便除錯與統計。
    source_type: Mapped[str] = mapped_column(String(16), nullable=False)
    # 抽取後的純文字原文（FR-8 必須保存）。
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    # "parsed" / "failed" —— LLM 結構化解析結果狀態（FR-10 容錯）。
    parse_status: Mapped[str] = mapped_column(String(16), nullable=False)
    # 解析失敗訊息，供前端提示手動修正、供稽核（FR-52）。
    parse_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    versions: Mapped[list["ResumeVersion"]] = relationship(
        back_populates="resume", cascade="all, delete-orphan"
    )


class ResumeVersion(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "resume_versions"

    resume_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("resumes.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    # 冗餘存一份 owner，使隔離查詢/清理不必每次 join resumes。
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    # 從 1 起遞增；current = 同一 resume_id 內最大者。
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    # "original"（初次解析）/ "edit"（手動編輯）/ 未來 "ai"（AI 建議版）。
    label: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'edit'"))
    # 結構化履歷資料（ResumeParsed.model_dump()）。
    parsed_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    resume: Mapped["Resume"] = relationship(back_populates="versions")


class ResumeEmbedding(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "resume_embeddings"

    # 向量隸屬於「某個版本」：編輯產生新版本時另建一組，舊版向量保留（歷史不破壞）。
    # 索引由 unique constraint 的前導欄位提供，不另建。
    resume_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("resume_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    # 冗餘 resume_id / user_id：清理與隔離查詢不必 join resume_versions（同 JobEmbedding）。
    resume_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("resumes.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    # "summary" / "skills" / "experience" —— 對應 build_resume_embedding_texts 的三種文字。
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    # 產生此向量的 embedding 模型（同 LLMCallLog.model 長度）。
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    # 768 維向量；provider 已 L2 正規化。查詢側（RETRIEVAL_QUERY）。
    # 不建 HNSW 索引：每版最多 3 筆、永遠按 version 取出後在 Python 端比對，
    # 不會成為相似度掃描的目標。
    vector: Mapped[list[float]] = mapped_column(Vector(_VECTOR_DIM), nullable=False)

    __table_args__ = (
        UniqueConstraint("resume_version_id", "kind", name="uq_resume_embeddings_version_kind"),
    )
