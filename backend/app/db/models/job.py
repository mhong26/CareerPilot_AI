"""Job / JobChunk / JobEmbedding models — 職缺原文、結構化解析與向量索引（FR-13~18）。

Job 存「一次匯入的事實」：原文、解析結果、解析與索引狀態。
JobChunk 存 section-aware 切塊後的每一段文字（Phase 6 引用出處的最小單位）。
JobEmbedding 存每個 chunk 的向量，與 chunk 一對一，供 pgvector 相似度搜尋。

切塊與向量是後續 match ranking（Phase 5）與 RAG skill gap（Phase 6）的資料地基。
"""

import uuid
from typing import Any

from pgvector.sqlalchemy import Vector  # 專案首次使用 pgvector 的 SQLAlchemy 型別
from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models.base import TimestampMixin, UUIDPKMixin
from app.db.session import Base

# 向量維度寫死（不讀 settings.embedding_dim）：create_all（本地測試）與 migration（CI）
# 產出的 DDL 必須一致、不受環境變數影響。變更此值須同步改 settings.embedding_dim 並出新 migration。
_VECTOR_DIM = 768


class Job(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "jobs"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    # 貼上的職缺原文（FR-18 必須保存）。
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)

    # 從 parsed_data 反正規化出來，供列表頁顯示而不必讀 JSONB。
    company: Mapped[str | None] = mapped_column(String(255), nullable=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # 結構化職缺資料（JobParsed.model_dump()）；解析失敗為 None。
    parsed_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    # "parsed" / "failed" —— LLM 結構化解析結果狀態（FR-10 容錯）。
    parse_status: Mapped[str] = mapped_column(String(16), nullable=False)
    parse_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # "indexed" / "failed" / "skipped" —— 向量索引狀態。
    # skipped = 解析失敗或無 chunk；failed = embedding 呼叫失敗（chunk 仍存）。
    index_status: Mapped[str] = mapped_column(String(16), nullable=False)
    index_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    chunks: Mapped[list["JobChunk"]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="JobChunk.chunk_index",
    )


class JobChunk(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "job_chunks"

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    # 冗餘存一份 owner，使隔離查詢 / 檢索不必每次 join jobs（同 ResumeVersion）。
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    # 同一 job 內從 0 起遞增的順序。
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)

    # 來源段落標籤（"overview" / "responsibilities" / "required_skills" ...）。
    section: Mapped[str] = mapped_column(String(32), nullable=False)

    # 實際被送去 embedding 的文字（含段落前綴）。
    content: Mapped[str] = mapped_column(Text, nullable=False)

    job: Mapped["Job"] = relationship(back_populates="chunks")

    embedding: Mapped["JobEmbedding | None"] = relationship(
        back_populates="chunk", cascade="all, delete-orphan"
    )


class JobEmbedding(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "job_embeddings"

    # 與 chunk 一對一（unique）。
    chunk_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("job_chunks.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )

    # 冗餘 job_id / user_id：Phase 6 檢索可只在單一 job 或單一 user 的向量內搜尋而不 join。
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    # 產生此向量的 embedding 模型（同 LLMCallLog.model 長度）。
    model: Mapped[str] = mapped_column(String(128), nullable=False)

    # 768 維向量；provider 已 L2 正規化，故 cosine 與 dot product 等價。
    vector: Mapped[list[float]] = mapped_column(Vector(_VECTOR_DIM), nullable=False)

    chunk: Mapped["JobChunk"] = relationship(back_populates="embedding")

    # HNSW cosine 索引宣告在此（而非只在 migration）：本地 create_all 與 CI 的 alembic
    # 兩種建表途徑都要長出這個索引。HNSW 逐筆建圖，空表也能建、小資料集召回穩定。
    __table_args__ = (
        Index(
            "ix_job_embeddings_vector",
            "vector",
            postgresql_using="hnsw",
            postgresql_ops={"vector": "vector_cosine_ops"},
        ),
    )
