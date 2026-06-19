"""estimate_cost 的純函式測試。"""

from decimal import Decimal

from app.ai.llm.pricing import estimate_cost


def test_known_model_cost():
    # gemini-2.5-flash-lite：input 0.10、output 0.40（USD / 1M tokens）。
    # 1,000,000 in + 1,000,000 out = 0.10 + 0.40 = 0.50
    assert estimate_cost("gemini-2.5-flash-lite", 1_000_000, 1_000_000) == Decimal("0.500000")


def test_unknown_model_returns_zero():
    assert estimate_cost("some-model-we-dont-price", 1000, 1000) == Decimal("0")


def test_embedding_has_no_output_cost():
    # gemini-embedding-001：input 0.15、output 0。output tokens 不計費。
    assert estimate_cost("gemini-embedding-001", 1_000_000, 999) == Decimal("0.150000")
