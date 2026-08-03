"""LLM 呼叫帳本 — 每次呼叫 LLM/embedding 的用量、延遲、成本與結果。

存在理由：LLM 呼叫按 token 計費，必須可稽核成本（FR-65）、保存失敗紀錄供除錯
（FR-61），並提供 Phase 9 評估所需的原始延遲 / 錯誤資料（NFR-1、NFR-5）。

第 3 步起的 Gemini 實作會在每次呼叫（成功或失敗）後寫入一筆。
"""

import uuid
from decimal import Decimal

from sqlalchemy import Boolean, ForeignKey, Integer, Numeric, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import TimestampMixin, UUIDPKMixin
from app.db.session import Base


class LLMCallLog(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "llm_call_logs"

    # 可空：測試 / eval / 系統內部呼叫沒有對應使用者。
    # SET NULL：使用者被刪除時保留帳本，只清掉 owner（不破壞歷史紀錄）。
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    # 實際成功（或最後嘗試）的模型；fallback 觸發時記的是 fallback 模型，
    # 不必然等於 settings.gemini_model（FR-67）。
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    # "generate" / "generate_structured" / "embed" / "agent_planner"（Phase 7
    # planner 呼叫，FR-65）—— 方便依操作類型分析。
    operation: Mapped[str] = mapped_column(String(32), nullable=False)
    # prompt 的 sha256 十六進位指紋（不存原文：省空間 + 保護隱私 + 可偵測重複）。
    prompt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    # 估計美金成本用 Numeric/Decimal —— 金額不可用浮點數（會有累積誤差）。
    cost_estimate: Mapped[Decimal] = mapped_column(
        Numeric(10, 6), nullable=False, server_default=text("0")
    )
    # --- malformed-rate 追蹤（FR-68）---
    # attempts：協調層發出的「生成請求」次數（primary 驗證重試 × 各模型，含 fallback；
    # 不含 tenacity 網路層重試，該層次數無法可靠取得）。
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    # 最終回傳物件是否經 repair_json 修復而來。
    repair_used: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    # 是否切換到 fallback model。
    fallback_used: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    # "success" / "error"。
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    # 失敗原因（含 schema 驗證失敗訊息），供稽核除錯（FR-61）。
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
