"""Resume / ResumeVersion models — 使用者履歷原文與結構化解析的版本歷史。

Resume 存「一次上傳的事實」：原文、來源型別、解析狀態。
ResumeVersion 存「結構化資料的歷史快照」：初次解析為 v1，每次編輯 +1（FR-12）。
目前生效版本 = 該 resume 中 version_number 最大者（不另設指標欄，避免循環 FK）。
"""

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models.base import TimestampMixin, UUIDPKMixin
from app.db.session import Base

if TYPE_CHECKING:
    pass


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
    # 解析失敗訊息，供前端提示手動修正、供稽核（FR-61）。
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
