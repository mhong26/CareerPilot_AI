"""Rubric suite（ER-3 第 2 項）：tailored-resume 建議品質的 LLM-as-judge 評分。

受測物 = kit_generation suite 產出的 artifacts（``eval/.cache/artifacts/``），
固定 rubric 四維度（relevance / specificity / actionability / alignment，
1-5 分、含錨點描述）。經 ``langsmith.evaluate()`` 執行、traces 留在
LangSmith；judge 走快取（一份 artifact 一次呼叫、永不重評）。
"""

import json

import eval._bootstrap  # noqa: F401  # isort: split
from eval.config import EvalConfig
from eval.datasets.schema import Scenario
from eval.harness import Providers, Timings
from eval.langsmith.evaluators import (
    RUBRIC_DIMENSIONS,
    JudgeCallError,
    JudgeQuotaExhausted,
    call_judge,
    judge_rubric,
)
from eval.langsmith.sync_datasets import DATASET_RUBRIC, sync_all
from eval.metrics import mean
from eval.suites import SuiteResult, fmt
from eval.suites.kit_generation import artifact_path

SUITE = "rubric"


def run(
    session_maker,
    cfg: EvalConfig,
    manifest: dict,
    scenarios: list[Scenario],
    providers: Providers,
    timings: Timings,
) -> SuiteResult:
    result = SuiteResult(name=SUITE)
    todo = scenarios[: cfg.limit] if cfg.limit else scenarios

    artifacts: dict[str, dict] = {}
    for scenario in todo:
        path = artifact_path(cfg, scenario.scenario_id)
        try:
            artifacts[scenario.scenario_id] = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            result.notes.append(
                f"{scenario.scenario_id}: no generated artifact — run the kit_generation "
                "suite first"
            )
            result.partial = True
    if not artifacts:
        result.skipped = True
        result.notes.append("no artifacts to score")
        return result

    from langsmith import Client, evaluate

    # dataset 涵蓋檢查 + 自動同步（sync_all 全量）＋不可用時降級 skipped，
    # 理由與作法同 hallucination suite。
    try:
        client = Client()
        try:
            cloud_ids = {
                (e.metadata or {}).get("scenario_id")
                for e in client.list_examples(dataset_name=DATASET_RUBRIC)
            }
        except Exception:
            cloud_ids = set()
        if not set(artifacts) <= cloud_ids:
            sync_all()
    except Exception as exc:
        result.skipped = True
        result.notes.append(f"LangSmith unavailable ({exc}) — judge suite not run")
        return result

    scores: dict[str, dict] = {}
    quota = {"exhausted": False, "reason": ""}

    def target(inputs: dict) -> dict:
        material = artifacts.get(inputs["scenario_id"])
        return {
            "scenario_id": inputs["scenario_id"],
            "artifact": material["payload"] if material else None,
            "context": material["context"] if material else None,
        }

    def evaluator(run_obj, example) -> dict:
        outputs = run_obj.outputs or {}
        sid = outputs.get("scenario_id", "?")
        if outputs.get("artifact") is None:
            return {"key": "rubric_mean", "score": None, "comment": "not prepared in this run"}
        if quota["exhausted"]:
            return {"key": "rubric_mean", "score": None, "comment": "judge quota exhausted"}
        try:
            score = call_judge(
                judge_rubric,
                providers.judge,
                artifact=outputs["artifact"],
                context=outputs["context"],
            )
        except JudgeQuotaExhausted as exc:
            quota["exhausted"] = True
            quota["reason"] = str(exc)
            return {"key": "rubric_mean", "score": None, "comment": "judge quota exhausted"}
        except JudgeCallError as exc:
            # 單次失敗不放棄整輪；該情境本輪不計分，重跑可續。
            return {
                "key": "rubric_mean",
                "score": None,
                "comment": f"judge call failed: {exc}"[:400],
            }
        values = {dim: getattr(score, dim) for dim in RUBRIC_DIMENSIONS}
        scores[sid] = {**values, "rationale": score.rationale}
        overall = sum(values.values()) / len(values)
        return {"key": "rubric_mean", "score": overall, "comment": score.rationale[:400]}

    try:
        evaluate(
            target,
            data=DATASET_RUBRIC,
            evaluators=[evaluator],
            experiment_prefix="careerpilot-rubric",
            max_concurrency=1,
        )
    except Exception as exc:  # noqa: BLE001 — LangSmith 連線失敗等：如實降級
        if not scores:
            # 一分都沒評到：整個 suite 標 skipped，不覆蓋既有成功結果。
            result.skipped = True
            result.notes.append(f"langsmith.evaluate failed before any judgment ({exc})")
            return result
        result.notes.append(f"langsmith.evaluate failed mid-run: {exc}")
        result.partial = True

    if quota["exhausted"]:
        result.partial = True
        result.notes.append(
            f"judge quota exhausted mid-run ({quota['reason'][:120]}…); scored "
            f"{len(scores)}/{len(artifacts)} artifacts — re-run later to continue"
        )
    elif len(scores) < len(artifacts):
        result.partial = True
        result.notes.append(
            f"scored {len(scores)}/{len(artifacts)} artifacts this run "
            "(some judge calls failed, non-quota) — re-run to retry"
        )
    if not scores:
        # 一分都沒評到（多半是額度一開始就耗盡）：空表不得覆蓋既有結果。
        result.skipped = True
        reason = quota["reason"][:120] if quota["exhausted"] else "no score returned"
        result.notes.append(f"no artifact scored this run ({reason}…)")
        return result

    dim_rows = []
    for dim in RUBRIC_DIMENSIONS:
        values = [s[dim] for s in scores.values()]
        distribution = " / ".join(str(sum(1 for v in values if v == g)) for g in range(1, 6))
        dim_rows.append([dim, fmt(mean(values), 2) if values else "n/a", distribution])
    overall_values = [
        sum(s[dim] for dim in RUBRIC_DIMENSIONS) / len(RUBRIC_DIMENSIONS)
        for s in scores.values()
    ]
    result.tables.append(
        {
            "title": f"Rubric scores — tailored-resume suggestions (n={len(scores)} artifacts)",
            "headers": ["Dimension", "Mean (1-5)", "Distribution (1/2/3/4/5)"],
            "rows": [
                *dim_rows,
                ["**overall**", fmt(mean(overall_values), 2) if overall_values else "n/a", ""],
            ],
        }
    )
    result.tables.append(
        {
            "title": "Per-scenario scores",
            "headers": ["Scenario", *RUBRIC_DIMENSIONS],
            "rows": [
                [sid, *(s[dim] for dim in RUBRIC_DIMENSIONS)] for sid, s in sorted(scores.items())
            ],
        }
    )
    result.notes.append(
        "Fixed rubric with 1/3/5 anchors per dimension (see eval/langsmith/evaluators.py); "
        "the judge is instructed to score strictly and reserve 5 for excellent work."
    )
    result.data = {"scores": scores, "n": len(scores)}
    return result
