#!/usr/bin/env python3
"""CareerPilot AI — Evaluation Runner（Phase 8，ER-1~9 / FR-59 / NFR-1、5）。

一鍵：``python eval/run_eval.py`` → seed（冪等）→ 本地 suites → judge suites
（額度 / key 允許時）→ 產出 ``docs/eval_report.md``。

額度友善設計：所有 LLM / embedding 呼叫走磁碟快取（重跑零 API）、
``--limit N`` 抽樣、suite 結果落盤可跨日續跑、judge 額度耗盡標 partial
不炸整輪。無 ``GEMINI_API_KEY`` 時本地指標吃快取照算、judge 類跳過並在
報告註明（ER-8）。
"""

import sys
from pathlib import Path

# 支援 `python eval/run_eval.py` 直跑：sys.path[0] 會是 eval/ 本身，
# 除了補 repo root，還必須把 eval/ 從 path 移掉——否則 eval/langsmith/
# 會以頂層 `langsmith` 之名遮蔽 pip 套件。
if __package__ in (None, ""):
    _here = Path(__file__).resolve().parent
    sys.path[:] = [p for p in sys.path if p and Path(p).resolve() != _here]
    sys.path.insert(0, str(_here.parent))

import os  # noqa: E402

import eval._bootstrap  # noqa: F401,E402
from eval import report as report_mod  # noqa: E402
from eval import seed as seed_mod  # noqa: E402
from eval.config import (  # noqa: E402
    JUDGE_SUITES,
    LOCAL_SUITES,
    EvalConfig,
    build_parser,
    from_args,
)
from eval.datasets.schema import load_all  # noqa: E402
from eval.harness import (  # noqa: E402
    EvalEnvironmentError,
    Timings,
    build_providers,
    ensure_database,
    session_factory,
)
from eval.suites import SuiteResult  # noqa: E402


def _judge_skip_reason(cfg: EvalConfig, offline: bool) -> str | None:
    from app.core.config import settings

    if cfg.skip_judge:
        return "--skip-judge flag"
    if offline:
        return "GEMINI_API_KEY not set"
    if not (os.environ.get("LANGSMITH_API_KEY") or settings.langsmith_api_key):
        return "LANGSMITH_API_KEY not set (judge suites run through langsmith.evaluate)"
    return None


def main(argv: list[str] | None = None) -> int:
    cfg = from_args(build_parser().parse_args(argv))

    scenarios = load_all(cfg.dataset_dir)
    todo = scenarios[: cfg.limit] if cfg.limit else scenarios
    print(f"[eval] {len(scenarios)} scenario(s) in dataset, processing {len(todo)}")

    providers = build_providers(cfg)
    if providers.offline:
        print("[eval] GEMINI_API_KEY not set — offline mode (cache-only; misses will fail)")
    timings = Timings(cfg.cache_dir / "timings.json")

    try:
        engine = ensure_database(cfg)
    except EvalEnvironmentError as exc:
        print(f"[eval] FATAL: {exc}")
        return 2
    session_maker = session_factory(engine)

    # --- seed（冪等；--only seed 可單獨執行；單一情境失敗只縮小範圍）----------
    with session_maker() as db:
        manifest = seed_mod.seed_scenarios(db, todo, providers.gen, timings, cfg)
    unseeded = [s.scenario_id for s in todo if s.scenario_id not in manifest]
    if unseeded:
        print(f"[eval] running suites WITHOUT unseeded scenario(s): {', '.join(unseeded)}")
        todo = [s for s in todo if s.scenario_id in manifest]
    if not todo:
        print("[eval] FATAL: no scenario seeded successfully — fix quota/DB and re-run")
        return 2
    if cfg.only == ("seed",):
        print("[eval] seed-only run complete")
        return 0

    # --- 本地 suites ----------------------------------------------------------
    from eval.suites import kit_generation, matching, rag, reliability, system

    local_modules = {
        "matching": matching,
        "rag": rag,
        "kit_generation": kit_generation,
        "reliability": reliability,
        "system": system,
    }
    results: dict[str, SuiteResult] = {}
    for name in LOCAL_SUITES:
        if not cfg.wants(name):
            continue
        print(f"[eval] running suite: {name}")
        result = local_modules[name].run(session_maker, cfg, manifest, todo, providers, timings)
        if not result.skipped:  # skipped 不落盤：保留上一份成功結果供報告合併
            result.save(cfg)
        results[name] = result

    # --- judge suites（LangSmith + judge model）-------------------------------
    judge_reason = _judge_skip_reason(cfg, providers.offline)
    wanted_judges = [name for name in JUDGE_SUITES if cfg.wants(name)]
    if wanted_judges and judge_reason is None:
        if cfg.sync_langsmith:
            from eval.langsmith.sync_datasets import sync_all

            print("[eval] syncing datasets to LangSmith")
            sync_all(scenarios)
        from eval.suites import hallucination, rubric

        judge_modules = {"hallucination": hallucination, "rubric": rubric}
        for name in wanted_judges:
            print(f"[eval] running judge suite: {name}")
            result = judge_modules[name].run(
                session_maker, cfg, manifest, todo, providers, timings
            )
            if not result.skipped:  # 同上：skipped 不得覆蓋既有成功結果
                result.save(cfg)
            results[name] = result
    else:
        for name in wanted_judges:
            # 不落盤：保留先前成功的 judge 結果供報告合併，僅在本次報告註明跳過。
            results[name] = SuiteResult(
                name=name, skipped=True, notes=[f"skipped: {judge_reason}"]
            )
            print(f"[eval] skipping judge suite {name}: {judge_reason}")

    # --- 報告 -----------------------------------------------------------------
    path = report_mod.render_report(
        results, cfg, scenario_count=len(scenarios), offline=providers.offline
    )
    print(f"[eval] report written to {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
