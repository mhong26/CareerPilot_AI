"""LLM 估價表 —— 把 token 用量換算成估計美金成本（FR-65）。

價格時常變動，集中在這一處維護。數值是「USD / 每百萬 (1M) tokens」。
未知 model 回 ``Decimal("0")``（成本本來就是估計值，找不到價格不該讓主流程失敗）。
"""

from decimal import Decimal

_PER_MILLION = Decimal(1_000_000)

# model -> (input 單價, output 單價)，單位 USD / 1M tokens。
_PRICING: dict[str, tuple[Decimal, Decimal]] = {
    # 現行 primary / fallback（FR-67）。
    "gemini-3.5-flash-lite": (Decimal("0.30"), Decimal("2.50")),
    "gemini-3.6-flash": (Decimal("1.50"), Decimal("7.50")),
    # 舊配置保留：歷史 LLMCallLog 仍參照這些 model 名。
    "gemini-2.5-flash-lite": (Decimal("0.10"), Decimal("0.40")),
    "gemini-2.5-flash": (Decimal("0.30"), Decimal("2.50")),
    # 前 fallback 模型；採 ≤200k prompt tokens 級距的單價。
    "gemini-2.5-pro": (Decimal("1.25"), Decimal("10.00")),
    # Embedding 只計 input tokens（無 output）。
    "gemini-embedding-001": (Decimal("0.15"), Decimal("0")),
}


def estimate_cost(model: str, tokens_in: int, tokens_out: int) -> Decimal:
    """依模型單價估算單次呼叫成本（USD）。未知 model 回 0。"""
    price = _PRICING.get(model)
    if price is None:
        return Decimal("0")
    input_price, output_price = price
    cost = (Decimal(tokens_in) * input_price + Decimal(tokens_out) * output_price) / _PER_MILLION
    # 量化到 6 位小數，對齊 DB 欄位 Numeric(10, 6)。
    return cost.quantize(Decimal("0.000001"))
