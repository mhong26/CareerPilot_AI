"""Reliability suite（FR-59 / ER-7）：malformed-response rate 與 fallback 統計。

資料來源：eval DB 的 ``llm_call_logs``——獨立資料庫，裡面**只有** eval 流量，
統計免除使用者過濾。快取命中列以 ``model LIKE '%+cached'`` 標記，一律排除
（cache hit 不是真實 API 行為）。

定義（與 SRS FR-59 對齊）：
- **raw malformed rate**：首次嘗試即失敗的比率。近似為
  ``attempts > 1 OR repair_used OR status='error'``——已知 caveat：attempts
  同時計入網路層換模與 schema retry，transport 失敗與 schema 失敗在帳面上
  無法完全區分，此為高估方向的保守近似（報告如實註明）。
- **final malformed rate**：經 retry / repair / fallback 後仍失敗
  （``StructuredOutputError``）的比率；以錯誤訊息中的 schema 失敗標記辨識。
"""

from sqlalchemy import text

import eval._bootstrap  # noqa: F401  # isort: split
from eval.config import EvalConfig
from eval.datasets.schema import Scenario
from eval.harness import Providers, Timings
from eval.suites import SuiteResult

SUITE = "reliability"

# GeminiProvider 的 StructuredOutputError 訊息標記（gemini.py）；
# 用它把「schema 最終失敗」從一般 LLMError（網路 / 安全阻擋）中分出來。
_SCHEMA_FAILURE_MARKER = "%不符 schema%"

_STRUCTURED_SQL = text(
    """
    SELECT
      count(*)                                                             AS total,
      count(*) FILTER (WHERE attempts > 1 OR repair_used OR status = 'error') AS raw_bad,
      count(*) FILTER (WHERE status = 'error')                             AS errors,
      count(*) FILTER (WHERE status = 'error' AND error LIKE :marker)      AS final_malformed,
      count(*) FILTER (WHERE repair_used)                                  AS repaired,
      count(*) FILTER (WHERE fallback_used)                                AS fallbacks
    FROM llm_call_logs
    WHERE operation = 'generate_structured' AND model NOT LIKE '%+cached'
    """
)

_PER_OPERATION_SQL = text(
    """
    SELECT
      operation,
      count(*)                                  AS calls,
      count(*) FILTER (WHERE status = 'error')  AS errors,
      count(*) FILTER (WHERE fallback_used)     AS fallbacks,
      coalesce(sum(tokens_in), 0)               AS tokens_in,
      coalesce(sum(tokens_out), 0)              AS tokens_out,
      coalesce(sum(cost_estimate), 0)           AS cost
    FROM llm_call_logs
    WHERE model NOT LIKE '%+cached'
    GROUP BY operation
    ORDER BY operation
    """
)


def _rate(numerator: int, denominator: int) -> str:
    if denominator == 0:
        return "n/a"
    return f"{numerator / denominator:.2%} ({numerator}/{denominator})"


def run(
    session_maker,
    cfg: EvalConfig,
    manifest: dict,
    scenarios: list[Scenario],
    providers: Providers,
    timings: Timings,
) -> SuiteResult:
    result = SuiteResult(name=SUITE)

    with session_maker() as db:
        s = db.execute(_STRUCTURED_SQL, {"marker": _SCHEMA_FAILURE_MARKER}).one()
        per_op = db.execute(_PER_OPERATION_SQL).all()

    result.tables.append(
        {
            "title": "Malformed-response rates (structured-output calls, live traffic only)",
            "headers": ["Metric", "Value"],
            "rows": [
                ["Raw malformed rate (first attempt failed)", _rate(s.raw_bad, s.total)],
                ["Final malformed rate (target < 1%, measure-and-report)",
                 _rate(s.final_malformed, s.total)],
                ["Repair (JSON fix) usage rate", _rate(s.repaired, s.total)],
                ["Model fallback trigger rate", _rate(s.fallbacks, s.total)],
                ["Error rate incl. transport errors", _rate(s.errors, s.total)],
            ],
        }
    )
    result.tables.append(
        {
            "title": "Per-operation call ledger (LLMCallLog, live traffic only)",
            "headers": ["Operation", "Calls", "Errors", "Fallbacks", "Tokens in", "Tokens out",
                        "Est. cost (USD)"],
            "rows": [
                [r.operation, r.calls, r.errors, r.fallbacks, r.tokens_in, r.tokens_out,
                 f"{r.cost:.4f}"]
                for r in per_op
            ],
        }
    )
    result.notes.append(
        "Raw rate caveat: `attempts` counts both schema-validation retries and "
        "transport-level model switches, so transport failures inflate the raw rate — "
        "it is a conservative upper bound on true first-attempt malformed output."
    )
    result.notes.append(
        "Rows with model marked '+cached' (eval cache hits) are excluded everywhere; "
        "they represent replayed results, not live API behaviour."
    )
    result.data = {
        "structured_total": s.total,
        "raw_bad": s.raw_bad,
        "final_malformed": s.final_malformed,
        "repaired": s.repaired,
        "fallbacks": s.fallbacks,
        "errors": s.errors,
    }
    return result
