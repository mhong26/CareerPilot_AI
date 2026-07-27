"""record_call 的資料層測試（寫入 test DB，比照既有 auth 測試模式）。"""

import hashlib
from decimal import Decimal

from sqlalchemy import select

from app.ai.llm.base import TokenUsage
from app.ai.llm.pricing import estimate_cost
from app.db.models import LLMCallLog
from app.services.llm_call_log_service import record_call


def test_record_call_persists_row(db_session):
    usage = TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150)
    log = record_call(
        db_session,
        provider="gemini",
        model="gemini-2.5-flash-lite",
        operation="generate",
        prompt="hello world",
        usage=usage,
        latency_ms=123,
        status="success",
    )

    # 從 DB 重新查出，確認真的寫進去。
    fetched = db_session.scalar(select(LLMCallLog).where(LLMCallLog.id == log.id))
    assert fetched is not None
    assert fetched.tokens_in == 100
    assert fetched.tokens_out == 50
    assert fetched.status == "success"
    # prompt 只存 sha256 指紋，不存原文。
    assert fetched.prompt_hash == hashlib.sha256(b"hello world").hexdigest()
    assert "hello world" not in fetched.prompt_hash
    # 成本與單價表一致。
    assert fetched.cost_estimate == estimate_cost("gemini-2.5-flash-lite", 100, 50)


def test_record_call_stores_error_status(db_session):
    log = record_call(
        db_session,
        provider="gemini",
        model="gemini-2.5-flash-lite",
        operation="generate_structured",
        prompt="bad prompt",
        usage=TokenUsage(),
        latency_ms=0,
        status="error",
        error="schema validation failed",
    )
    fetched = db_session.scalar(select(LLMCallLog).where(LLMCallLog.id == log.id))
    assert fetched.status == "error"
    assert fetched.error == "schema validation failed"


def test_record_call_defaults_for_new_columns(db_session):
    """未帶新參數 → attempts=1、repair/fallback=False（embed 與既有呼叫端不受影響）。"""
    log = record_call(
        db_session,
        provider="gemini",
        model="gemini-2.5-flash-lite",
        operation="embed",
        prompt="chunk text",
        usage=TokenUsage(),
        latency_ms=10,
        status="success",
    )
    fetched = db_session.scalar(select(LLMCallLog).where(LLMCallLog.id == log.id))
    assert fetched.attempts == 1
    assert fetched.repair_used is False
    assert fetched.fallback_used is False


def test_record_call_persists_fallback_metadata_and_cost(db_session):
    """新欄位照實寫入；cost 有給就原樣入帳（跨模型分價加總無法用單一單價重算）。"""
    log = record_call(
        db_session,
        provider="gemini",
        model="gemini-2.5-pro",
        operation="generate_structured",
        prompt="tricky prompt",
        usage=TokenUsage(prompt_tokens=40, completion_tokens=12, total_tokens=52),
        latency_ms=999,
        status="success",
        attempts=3,
        repair_used=True,
        fallback_used=True,
        cost=Decimal("0.004321"),
    )
    fetched = db_session.scalar(select(LLMCallLog).where(LLMCallLog.id == log.id))
    assert fetched.attempts == 3
    assert fetched.repair_used is True
    assert fetched.fallback_used is True
    assert fetched.cost_estimate == Decimal("0.004321")
    # 對照：沒帶 cost 時仍由 model + usage 查價（既有行為不變）。
    assert fetched.cost_estimate != estimate_cost("gemini-2.5-pro", 40, 12)
