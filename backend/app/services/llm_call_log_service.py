"""把單次 LLM 呼叫寫入帳本（llm_call_logs）。

與 provider 分離：provider 純呼叫 API 並回傳 LLMResult，呼叫端拿到結果（或捕捉到
錯誤）後呼叫 ``record_call`` 記一筆。如此 provider 無需依賴 DB，易於單元測試。
"""

import hashlib
import uuid
from decimal import Decimal

from sqlalchemy.orm import Session

from app.ai.llm.base import TokenUsage
from app.ai.llm.pricing import estimate_cost
from app.db.models import LLMCallLog


def record_call(
    db: Session,
    *,
    provider: str,
    model: str,
    operation: str,
    prompt: str,
    usage: TokenUsage,
    latency_ms: int,
    status: str,
    error: str | None = None,
    user_id: uuid.UUID | None = None,
    attempts: int = 1,
    repair_used: bool = False,
    fallback_used: bool = False,
    cost: Decimal | None = None,
) -> LLMCallLog:
    """寫入一筆 LLM 呼叫紀錄並回傳。

    - prompt 只存 sha256 指紋（不存原文：省空間 + 保護隱私）。
    - cost 未給時由 model + token 用量查價估算；跨模型 fallback 的呼叫因各次嘗試
      單價不同，須由 provider 分價加總後帶入（單一單價算不準）。
    """
    prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    if cost is None:
        cost = estimate_cost(model, usage.prompt_tokens, usage.completion_tokens)
    log = LLMCallLog(
        user_id=user_id,
        provider=provider,
        model=model,
        operation=operation,
        prompt_hash=prompt_hash,
        tokens_in=usage.prompt_tokens,
        tokens_out=usage.completion_tokens,
        latency_ms=latency_ms,
        cost_estimate=cost,
        attempts=attempts,
        repair_used=repair_used,
        fallback_used=fallback_used,
        status=status,
        error=error,
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return log
