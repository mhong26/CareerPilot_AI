"""System suite（ER-9 / NFR-1 / NFR-5）：延遲百分位、error rate、coverage。

兩層延遲，報告分開呈現、不互相混算：

1. **操作層 wall-clock**（主要，對應 NFR-1 的 resume parse / job index /
   match / kit gen）：harness ``timed()`` 在 suite 執行時量測的端到端秒數。
   只統計「該次操作內發生真實 API 呼叫」的樣本——warm-cache 重播的毫秒級
   計時不代表任何真實延遲；冷跑樣本持久化於 timings.json，warm 重跑仍可
   報告先前收集的數字。
2. **LLM 呼叫層**（次要）：LLMCallLog 的 ``latency_ms`` 以 SQL
   ``percentile_cont`` 計 p50 / p95——單次 API 呼叫的延遲，不含服務端組裝。

Coverage（``--with-coverage``）：以 subprocess 對 backend 測試套件跑
pytest-cov，取 ``totals.percent_covered``（工作目標 >= 80%，實測值照報）。
"""

import json
import os
import subprocess

from sqlalchemy import text

import eval._bootstrap  # noqa: F401  # isort: split
from eval.config import REPO_ROOT, EvalConfig
from eval.datasets.schema import Scenario
from eval.harness import Providers, Timings
from eval.metrics import percentile
from eval.suites import SuiteResult

SUITE = "system"

# NFR-1 點名的操作（含對照說明）；resume parse 見下方 proxy caveat。
_OP_LABELS = {
    "resume_embed": "Resume embedding (per resume)",
    "job_index": "Job indexing (per job: chunk + embed + persist)",
    "match_run": "Match run (per scenario: 8 jobs scored + explained)",
    "skill_gap_run": "Skill-gap analysis (retrieve + rerank + generate)",
    "kit_generate": "Application-kit generation (tailored resume)",
}

_CALL_LATENCY_SQL = text(
    """
    SELECT
      operation,
      count(*) AS calls,
      percentile_cont(0.5)  WITHIN GROUP (ORDER BY latency_ms) AS p50,
      percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95,
      count(*) FILTER (WHERE status = 'error') AS errors
    FROM llm_call_logs
    WHERE model NOT LIKE '%+cached'
    GROUP BY operation
    ORDER BY operation
    """
)

_COVERAGE_DB_URL = "postgresql://careerpilot:careerpilot@localhost:5432/careerpilot"


def _coverage_percent(cfg: EvalConfig) -> tuple[float | None, str | None]:
    """跑 backend pytest-cov 拿實測 coverage；失敗回 (None, 原因)。

    用一般 ``careerpilot`` DB（conftest 會 TRUNCATE 它）——eval 資料在
    ``careerpilot_eval``，互不影響。
    """
    coverage_json = cfg.cache_dir / "coverage.json"
    env = dict(os.environ)
    env["DATABASE_URL"] = os.environ.get("COVERAGE_DATABASE_URL", _COVERAGE_DB_URL)
    try:
        proc = subprocess.run(
            [
                "python3", "-m", "pytest", "tests/", "-q",
                "--cov=app", f"--cov-report=json:{coverage_json}",
            ],
            cwd=REPO_ROOT / "backend",
            env=env,
            capture_output=True,
            text=True,
            timeout=1800,
        )
        if proc.returncode != 0:
            tail = (proc.stdout or proc.stderr).strip().splitlines()[-1:]
            return None, f"backend test run failed: {' '.join(tail)}"
        data = json.loads(coverage_json.read_text(encoding="utf-8"))
        return float(data["totals"]["percent_covered"]), None
    except Exception as exc:  # noqa: BLE001 — coverage 是可選項，任何失敗都降級為 n/a
        return None, str(exc)


def run(
    session_maker,
    cfg: EvalConfig,
    manifest: dict,
    scenarios: list[Scenario],
    providers: Providers,
    timings: Timings,
) -> SuiteResult:
    result = SuiteResult(name=SUITE)

    wall_rows = []
    for op, label in _OP_LABELS.items():
        samples = timings.cold_samples(op)
        p50 = percentile(samples, 50)
        p95 = percentile(samples, 95)
        wall_rows.append(
            [
                label,
                len(samples),
                "n/a" if p50 is None else f"{p50:.2f}s",
                "n/a" if p95 is None else f"{p95:.2f}s",
            ]
        )
    result.tables.append(
        {
            "title": "Operation latency — harness wall-clock (cold samples only)",
            "headers": ["Operation", "n", "p50", "p95"],
            "rows": wall_rows,
        }
    )

    with session_maker() as db:
        call_rows = db.execute(_CALL_LATENCY_SQL).all()
    result.tables.append(
        {
            "title": "Per-LLM-call latency — LLMCallLog (live traffic only)",
            "headers": ["Operation", "Calls", "p50", "p95", "Errors", "Error rate"],
            "rows": [
                [
                    r.operation,
                    r.calls,
                    f"{r.p50:.0f}ms" if r.p50 is not None else "n/a",
                    f"{r.p95:.0f}ms" if r.p95 is not None else "n/a",
                    r.errors,
                    f"{r.errors / r.calls:.2%}" if r.calls else "n/a",
                ]
                for r in call_rows
            ],
        }
    )

    coverage_row: list = ["Backend test coverage (working target >= 80%)", "not measured"]
    coverage_value: float | None = None
    if cfg.with_coverage:
        coverage_value, cov_error = _coverage_percent(cfg)
        if coverage_value is not None:
            coverage_row[1] = f"{coverage_value:.1f}%"
        else:
            coverage_row[1] = f"n/a ({cov_error})"
    else:
        coverage_row[1] = "not measured (run with --with-coverage)"
    result.tables.append(
        {
            "title": "Quality gates",
            "headers": ["Metric", "Value"],
            "rows": [coverage_row],
        }
    )

    result.notes.append(
        "Resume parsing latency caveat: eval seeds structured resumes by design (to keep "
        "ground truth frozen), so end-to-end parse latency is not exercised here; the "
        "generate_structured row in the per-call table is the closest proxy for the LLM "
        "portion of parsing."
    )
    result.notes.append(
        "Wall-clock percentiles use only samples whose operation made at least one live "
        "API call; warm-cache replays are excluded as they measure nothing real. "
        "Client-side free-tier pacing sleep (EVAL_GEN_MIN_INTERVAL) is subtracted from "
        "every sample — it is quota management, not system latency."
    )
    result.data = {
        "wall_clock": {
            op: {"n": len(timings.cold_samples(op)),
                 "p50": percentile(timings.cold_samples(op), 50),
                 "p95": percentile(timings.cold_samples(op), 95)}
            for op in _OP_LABELS
        },
        "coverage_percent": coverage_value,
    }
    return result
