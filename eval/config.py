"""Eval 執行設定（CLI flags + 環境變數的單一匯聚點）。

刻意不讀 ``settings.database_url``：root ``.env`` 的 DATABASE_URL 指向
Docker 內部主機名（``db:5432``），且 eval 一律使用**獨立資料庫**
``careerpilot_eval``——與開發 DB 隔離的理由見 plan（backend/tests/conftest.py
每個測試後 TRUNCATE 全表，共用 DB 會讓 pytest 摧毀 eval 資料與 ER-7 統計）。
"""

import argparse
import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_EVAL_DB_URL = "postgresql://careerpilot:careerpilot@localhost:5432/careerpilot_eval"
DEFAULT_JUDGE_MODEL = "gemini-3.6-flash"  # ER-6：較 primary 強一級的同 provider 模型

# 本地 suite（不需 judge / LangSmith）與 judge suite 的執行順序。
LOCAL_SUITES = ("matching", "rag", "kit_generation", "reliability", "system")
JUDGE_SUITES = ("hallucination", "rubric")
ALL_SUITES = LOCAL_SUITES + JUDGE_SUITES


@dataclass
class EvalConfig:
    eval_db_url: str = DEFAULT_EVAL_DB_URL
    cache_dir: Path = REPO_ROOT / "eval" / ".cache"
    dataset_dir: Path = REPO_ROOT / "eval" / "datasets" / "scenarios"
    report_path: Path = REPO_ROOT / "docs" / "eval_report.md"
    judge_model: str = DEFAULT_JUDGE_MODEL
    only: tuple[str, ...] = ()  # 空 = 全部
    skip_judge: bool = False
    limit: int | None = None  # 每個 suite 最多處理前 N 個情境
    no_cache: bool = False
    reseed: tuple[str, ...] | None = None  # None=不 reseed；()=全部；(sNN,...)=指定
    with_coverage: bool = False
    sync_langsmith: bool = False

    @property
    def results_dir(self) -> Path:
        return self.cache_dir / "results"

    def wants(self, suite: str) -> bool:
        """該 suite 是否在本次執行範圍（--only 未指定時全跑）。"""
        return not self.only or suite in self.only


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_eval",
        description="CareerPilot AI evaluation runner (Phase 8) — writes docs/eval_report.md",
    )
    parser.add_argument(
        "--only",
        nargs="+",
        choices=[*ALL_SUITES, "seed"],
        default=None,
        metavar="SUITE",
        help=f"run only these suites (choices: seed, {', '.join(ALL_SUITES)})",
    )
    parser.add_argument(
        "--skip-judge",
        action="store_true",
        help="skip LLM-as-judge suites (hallucination, rubric)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="process only the first N scenarios per suite (quota control)",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="bypass the on-disk LLM/embedding cache (measure live variance; burns quota)",
    )
    parser.add_argument(
        "--reseed",
        nargs="*",
        default=None,
        metavar="SCENARIO",
        help="delete and re-seed the given scenario ids (no args = all scenarios)",
    )
    parser.add_argument(
        "--db-url",
        default=None,
        help=f"eval database URL (default: $EVAL_DATABASE_URL or {DEFAULT_EVAL_DB_URL})",
    )
    parser.add_argument(
        "--with-coverage",
        action="store_true",
        help="run backend pytest with coverage and include the %% in the report (needs dev DB)",
    )
    parser.add_argument(
        "--report-path",
        default=None,
        help="override the output report path (default: docs/eval_report.md)",
    )
    parser.add_argument(
        "--sync-langsmith",
        action="store_true",
        help="sync local datasets to LangSmith before running judge suites",
    )
    return parser


def from_args(ns: argparse.Namespace) -> EvalConfig:
    cfg = EvalConfig()
    cfg.eval_db_url = ns.db_url or os.environ.get("EVAL_DATABASE_URL") or DEFAULT_EVAL_DB_URL
    cfg.judge_model = os.environ.get("EVAL_JUDGE_MODEL") or DEFAULT_JUDGE_MODEL
    cfg.only = tuple(ns.only) if ns.only else ()
    cfg.skip_judge = ns.skip_judge
    cfg.limit = ns.limit
    cfg.no_cache = ns.no_cache
    cfg.reseed = tuple(ns.reseed) if ns.reseed is not None else None
    cfg.with_coverage = ns.with_coverage
    cfg.sync_langsmith = ns.sync_langsmith
    if ns.report_path:
        cfg.report_path = Path(ns.report_path)
    return cfg
