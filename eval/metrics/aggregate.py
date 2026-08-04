"""彙總統計（ER-9 latency 百分位等）——純函式，不引入 numpy。

樣本量級為「25 情境 × 少數操作」，nearest-rank 百分位已足夠；引入 numpy
只為 percentile 不值得（eval extra 已有 sklearn，但 metrics 模組刻意保持
零依賴可測）。
"""

import math
from collections.abc import Iterable


def mean(values: Iterable[float], *, default: float = 0.0) -> float:
    """算術平均；空輸入回 ``default``（報告端顯示 0 而非炸掉）。"""
    items = list(values)
    if not items:
        return default
    return sum(items) / len(items)


def percentile(values: Iterable[float], p: float) -> float | None:
    """Nearest-rank 百分位（p ∈ (0, 100]）；空輸入回 None（報告端顯示 n/a）。

    Nearest-rank 定義：取排序後第 ``ceil(p/100 * n)`` 個（1-based）樣本值，
    回傳值必為實際觀測值（不內插）——樣本少時比線性內插誠實。
    """
    if not 0 < p <= 100:
        raise ValueError(f"p must be in (0, 100], got {p}")
    items = sorted(values)
    if not items:
        return None
    rank = math.ceil(p / 100 * len(items))
    return items[rank - 1]
