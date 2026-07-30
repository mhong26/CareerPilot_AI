"""Cross-encoder rerank（FR-26）。

兩段式檢索的第二段：bi-encoder（pgvector 向量搜尋）海選 top-k 後，由
cross-encoder 對 (query, passage) 成對精排。選本地
``cross-encoder/ms-marco-MiniLM-L-6-v2``（~90MB）而非 LLM rerank：零 API
成本、確定性、CPU 毫秒級，Phase 9 對 25 組資料反覆評估不消耗額度。

``sentence_transformers`` 在函式內 lazy import：app 啟動與不觸 rerank 的
測試不付 torch import 代價；模型載入為模組級單例。任何失敗由呼叫端降級
為向量排序（NFR-4）。
"""

from typing import Any, Protocol

RERANK_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"

_model: Any = None  # 模組級單例快取；首次 predict 才載入


class Reranker(Protocol):
    """Rerank 注入介面——router 的 ``get_reranker()`` 可在測試中被 override，
    CI 因此永遠不下載真模型。"""

    def predict(self, query: str, passages: list[str]) -> list[float]: ...


class CrossEncoderReranker:
    """真實 cross-encoder；建構零成本，首次 ``predict`` 才載入模型。"""

    model = RERANK_MODEL

    def predict(self, query: str, passages: list[str]) -> list[float]:
        global _model
        if _model is None:
            # lazy import：torch 啟動成本只在真正需要 rerank 時支付
            from sentence_transformers import CrossEncoder

            _model = CrossEncoder(RERANK_MODEL)
        scores = _model.predict([(query, passage) for passage in passages])
        return [float(s) for s in scores]  # numpy.float32 → float（JSONB 防護）


def rerank_order(scores: list[float], count: int) -> list[int]:
    """分數 desc 的索引排序；``sorted`` 穩定 → 同分保留向量序。

    長度不符丟 ValueError（呼叫端視同 rerank 失敗、降級向量序）。純函式，
    零 fixture 可測。
    """
    if len(scores) != count:
        raise ValueError(f"rerank returned {len(scores)} scores for {count} passages")
    return sorted(range(count), key=lambda i: -scores[i])
