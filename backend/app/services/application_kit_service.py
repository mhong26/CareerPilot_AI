"""Application kit 生成層 service（FR-31~44、FR-65）。

本模組目前只含三個 artifact 的「call + log + 降級」生成函式；agent 編排
（run_application_kit 等）由 Phase 7 Step 8 補上。

交易注意：``record_call`` 自帶 commit。agent run 天生交替「LLM 呼叫」與
「寫入」，無法維持 skill_gap_service 式的「單一最終 commit」不變式——
改以 ``save_artifact`` 逐 artifact commit（每個 artifact 是獨立原子單位，
run 中途失敗時已保存者仍有效，NFR-4）。

降級哲學（NFR-4）：LLM 生成失敗回 ``(None, error)`` 不丟例外，成功／失敗
都入 ``LLMCallLog``（malformed rate 與成本追蹤自動涵蓋，FR-65、FR-68）。
"""

import time
import uuid
from typing import TypeVar

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.ai.llm.base import LLMError, LLMProvider, StructuredOutputError, TokenUsage
from app.ai.parsers.kit_schema import (
    CoverLetterDraft,
    InterviewPrepSet,
    TailoredResumeSuggestions,
)
from app.ai.prompts.kit import (
    COVER_LETTER_SYSTEM,
    INTERVIEW_QS_SYSTEM,
    TAILORED_RESUME_SYSTEM,
)
from app.core.config import settings
from app.services.llm_call_log_service import record_call

T = TypeVar("T", bound=BaseModel)


def _generate_payload(
    db: Session,
    *,
    prompt: str,
    system: str,
    schema: type[T],
    provider: LLMProvider,
    user_id: uuid.UUID | None = None,
) -> tuple[T | None, str | None]:
    """單次 structured 生成：計時 → 生成 → 記帳 → 失敗回 ``(None, error)`` 不丟例外。

    與 ``skill_gap_service.generate_skill_gap_analysis`` 同模板，抽參數消除三份重複。
    """
    start = time.perf_counter()
    try:
        result = provider.generate_structured(prompt, schema, system=system)
    except (StructuredOutputError, LLMError) as exc:
        record_call(
            db,
            provider="gemini",
            model=getattr(exc, "model", "") or getattr(provider, "model", settings.gemini_model),
            operation="generate_structured",
            prompt=prompt,
            usage=getattr(exc, "usage", None) or TokenUsage(),
            latency_ms=int((time.perf_counter() - start) * 1000),
            status="error",
            error=str(exc),
            user_id=user_id,
            attempts=getattr(exc, "attempts", 1),
            repair_used=getattr(exc, "repair_used", False),
            fallback_used=getattr(exc, "fallback_used", False),
            cost=getattr(exc, "cost_estimate", None),
        )
        return None, str(exc)

    record_call(
        db,
        provider="gemini",
        model=result.model,
        operation="generate_structured",
        prompt=prompt,
        usage=result.usage,
        latency_ms=int((time.perf_counter() - start) * 1000),
        status="success",
        user_id=user_id,
        attempts=result.attempts,
        repair_used=result.repair_used,
        fallback_used=result.fallback_used,
        cost=result.cost_estimate,
    )
    return result.data, None


def generate_tailored_resume_payload(
    db: Session,
    *,
    prompt: str,
    provider: LLMProvider,
    user_id: uuid.UUID | None = None,
) -> tuple[TailoredResumeSuggestions | None, str | None]:
    """客製化履歷建議（FR-31~35）；prompt 由 caller 以 ``build_tailored_resume_prompt`` 建好。"""
    return _generate_payload(
        db,
        prompt=prompt,
        system=TAILORED_RESUME_SYSTEM,
        schema=TailoredResumeSuggestions,
        provider=provider,
        user_id=user_id,
    )


def generate_cover_letter_payload(
    db: Session,
    *,
    prompt: str,
    provider: LLMProvider,
    user_id: uuid.UUID | None = None,
) -> tuple[CoverLetterDraft | None, str | None]:
    """Cover letter 草稿（FR-37~39）；prompt 由 caller 以 ``build_cover_letter_prompt`` 建好。"""
    return _generate_payload(
        db,
        prompt=prompt,
        system=COVER_LETTER_SYSTEM,
        schema=CoverLetterDraft,
        provider=provider,
        user_id=user_id,
    )


def generate_interview_prep_payload(
    db: Session,
    *,
    prompt: str,
    provider: LLMProvider,
    user_id: uuid.UUID | None = None,
) -> tuple[InterviewPrepSet | None, str | None]:
    """面試準備題組（FR-41~43）；prompt 由 caller 以 ``build_interview_qs_prompt`` 建好。"""
    return _generate_payload(
        db,
        prompt=prompt,
        system=INTERVIEW_QS_SYSTEM,
        schema=InterviewPrepSet,
        provider=provider,
        user_id=user_id,
    )
