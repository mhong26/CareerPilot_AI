"""排序品質指標（ER-1 / ER-3 / ER-5）：Precision@K、Reciprocal Rank、MRR。

純函式：matching（job 排名）與 RAG retrieval（chunk 排名）兩個 suite 共用
同一套實作，id 型別不拘（job_key 字串或 chunk UUID 皆可）。

約定：
- ``ranked`` 為系統輸出的排序（最相關在前）；重複 id 只計首次出現。
- ``relevant`` 為 ground truth 相關集合；空集合時各指標定義為 0.0
  （「無物可找」視同找不到，不製造除零特例）。
- Precision@K 分母固定為 K（經典定義）：相關數 < K 時上限自然 < 1，
  由報告端說明，不在此偷換分母。
"""

import math
from collections.abc import Hashable, Iterable, Mapping, Sequence
from collections.abc import Set as AbstractSet


def _dedup(ranked: Sequence[Hashable]) -> list[Hashable]:
    """保序去重：排序輸出若含重複 id，只有首次出現有意義。"""
    seen: set[Hashable] = set()
    out: list[Hashable] = []
    for item in ranked:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def precision_at_k(ranked: Sequence[Hashable], relevant: AbstractSet[Hashable], k: int) -> float:
    """前 K 名中相關項目的比例；分母固定為 K。"""
    if k <= 0:
        raise ValueError(f"k must be positive, got {k}")
    if not relevant:
        return 0.0
    top = _dedup(ranked)[:k]
    return sum(1 for item in top if item in relevant) / k


def reciprocal_rank(ranked: Sequence[Hashable], relevant: AbstractSet[Hashable]) -> float:
    """第一個相關項目名次的倒數；完全沒命中回 0.0。"""
    if not relevant:
        return 0.0
    for position, item in enumerate(_dedup(ranked), start=1):
        if item in relevant:
            return 1.0 / position
    return 0.0


def ndcg_at_k(
    ranked: Sequence[Hashable], grades: Mapping[Hashable, int], k: int
) -> float:
    """Graded NDCG@K（gain = 2^grade − 1，log2 位置折扣）。

    P@K 把 graded 標註二值化後，只要兩系統都把相關項塞進前 K 名就分不出
    高下——NDCG 用完整 0-3 分級與名次折扣，才照得出「把 grade-3 排第 1
    vs 第 3」「keyword-trap（grade 0）排第 2 vs 墊底」的差異。
    未出現在 ``grades`` 的 id 視為 grade 0；全零標註回 0.0。
    """
    if k <= 0:
        raise ValueError(f"k must be positive, got {k}")

    def dcg(grade_seq: Sequence[int]) -> float:
        return sum(
            (2**g - 1) / math.log2(pos + 1)
            for pos, g in enumerate(grade_seq[:k], start=1)
        )

    ideal = dcg(sorted(grades.values(), reverse=True))
    if ideal == 0:
        return 0.0
    actual = dcg([grades.get(item, 0) for item in _dedup(ranked)])
    return actual / ideal


def mrr(
    rankings: Iterable[tuple[Sequence[Hashable], AbstractSet[Hashable]]],
) -> float:
    """多筆 ``(ranked, relevant)`` 的 reciprocal rank 平均；空輸入回 0.0。"""
    pairs = list(rankings)
    if not pairs:
        return 0.0
    return sum(reciprocal_rank(ranked, relevant) for ranked, relevant in pairs) / len(pairs)
