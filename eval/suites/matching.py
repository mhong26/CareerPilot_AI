"""Matching suite（ER-3）：hybrid matcher vs TF-IDF baseline 的 P@K / MRR。

系統側呼叫真實 ``match_service.run_matches``（含 LLM 語意等價與 explanation
——經快取 provider，warm run 零 API）；baseline 側走 ``rank_jobs_tfidf``。
兩者的輸入文字基底相同（見 baselines/tfidf.py），ground truth 為 graded
標註以 grade >= RELEVANT_GRADE 二值化。
"""

import uuid

from app.ai.embeddings.resume_texts import build_resume_embedding_texts
from app.ai.rag.chunking import chunk_job
from app.services.match_service import run_matches

import eval._bootstrap  # noqa: F401  # isort: split
from eval import seed as seed_mod
from eval.baselines.tfidf import rank_jobs_tfidf
from eval.config import EvalConfig
from eval.datasets.schema import Scenario
from eval.harness import Providers, Timings
from eval.metrics import mean, mrr, ndcg_at_k, precision_at_k, reciprocal_rank
from eval.suites import SuiteResult, fmt, improvement_pct

SUITE = "matching"

# 兩大案例族（tag → 報告列名）：per-family breakdown 呈現 hybrid 的鑑別優勢。
_FAMILIES = {
    "semantic-no-keyword-overlap": "semantic-no-overlap scenarios",
    "keyword-trap": "keyword-trap scenarios",
}


def _scenario_metrics(
    predicted: list[str], relevant: set[str], grades: dict[str, int]
) -> dict[str, float]:
    return {
        "p3": precision_at_k(predicted, relevant, 3),
        "p5": precision_at_k(predicted, relevant, 5),
        "rr": reciprocal_rank(predicted, relevant),
        # graded NDCG：P@K 二值化下兩系統常同分（相關項都進前 K），
        # 只有名次折扣 + 0-3 分級照得出排序品質差異（trap 排第 2 vs 墊底）。
        "ndcg3": ndcg_at_k(predicted, grades, 3),
        "ndcg5": ndcg_at_k(predicted, grades, 5),
    }


def _macro(rows: list[dict], system: str) -> dict[str, float]:
    return {
        "p3": mean(r[system]["p3"] for r in rows),
        "p5": mean(r[system]["p5"] for r in rows),
        "mrr": mrr((r[system]["predicted"], set(r["relevant"])) for r in rows),
        "ndcg3": mean(r[system]["ndcg3"] for r in rows),
        "ndcg5": mean(r[system]["ndcg5"] for r in rows),
    }


def run(
    session_maker,
    cfg: EvalConfig,
    manifest: dict,
    scenarios: list[Scenario],
    providers: Providers,
    timings: Timings,
) -> SuiteResult:
    result = SuiteResult(name=SUITE)
    rows: list[dict] = []

    todo = scenarios[: cfg.limit] if cfg.limit else scenarios
    for scenario in todo:
        entry = manifest[scenario.scenario_id]
        relevant = scenario.relevant_job_keys

        with session_maker() as db:
            user = seed_mod.load_user(db, entry)
            id_to_key = seed_mod.job_id_to_key(entry)
            with timings.timed("match_run", providers.gen):
                _version, ranked, skipped = run_matches(
                    db,
                    user=user,
                    resume_id=uuid.UUID(entry["resume_id"]),
                    job_ids=list(id_to_key),
                    provider=providers.gen,
                )
            # ORM 屬性須在 session 存活時取用（commit 會 expire 屬性）。
            predicted_hybrid = [id_to_key[job.id] for _match, job in ranked]
        if skipped:
            result.notes.append(
                f"{scenario.scenario_id}: {len(skipped)} job(s) skipped by matcher "
                f"({[reason for _, reason in skipped]}) — check seeding"
            )
            result.partial = True

        resume_doc = "\n".join(text for _, text in build_resume_embedding_texts(scenario.resume))
        job_docs = {
            job.job_key: "\n".join(d.content for d in chunk_job(job.parsed))
            for job in scenario.jobs
        }
        predicted_tfidf = rank_jobs_tfidf(resume_doc, job_docs)

        rows.append(
            {
                "scenario_id": scenario.scenario_id,
                "tags": scenario.tags,
                "relevant": sorted(relevant),
                "hybrid": {
                    "predicted": predicted_hybrid,
                    **_scenario_metrics(
                        predicted_hybrid, relevant, scenario.ground_truth_ranking
                    ),
                },
                "tfidf": {
                    "predicted": predicted_tfidf,
                    **_scenario_metrics(
                        predicted_tfidf, relevant, scenario.ground_truth_ranking
                    ),
                },
            }
        )

    hybrid = _macro(rows, "hybrid")
    tfidf = _macro(rows, "tfidf")
    improvements = {
        m: improvement_pct(hybrid[m], tfidf[m])
        for m in ("p3", "p5", "mrr", "ndcg3", "ndcg5")
    }

    result.tables.append(
        {
            "title": f"Job matching — hybrid vs TF-IDF baseline (n={len(rows)} scenarios)",
            "headers": ["Metric", "Hybrid", "TF-IDF baseline", "Improvement"],
            "rows": [
                [
                    label,
                    fmt(hybrid[m]),
                    fmt(tfidf[m]),
                    "n/a" if improvements[m] is None else f"{improvements[m]:+.1f}%",
                ]
                for m, label in (
                    ("p3", "Precision@3"),
                    ("p5", "Precision@5"),
                    ("mrr", "MRR"),
                    ("ndcg3", "NDCG@3 (graded)"),
                    ("ndcg5", "NDCG@5 (graded)"),
                )
            ],
        }
    )

    family_rows = []
    for tag, label in _FAMILIES.items():
        subset = [r for r in rows if tag in r["tags"]]
        if not subset:
            continue
        h, t = _macro(subset, "hybrid"), _macro(subset, "tfidf")
        family_rows.append(
            [label, len(subset), fmt(h["ndcg5"]), fmt(t["ndcg5"]), fmt(h["p3"]), fmt(t["p3"])]
        )
    if family_rows:
        result.tables.append(
            {
                "title": "Case-family breakdown (where the two systems should diverge)",
                "headers": [
                    "Family", "n",
                    "Hybrid NDCG@5", "TF-IDF NDCG@5", "Hybrid P@3", "TF-IDF P@3",
                ],
                "rows": family_rows,
            }
        )

    result.tables.append(
        {
            "title": "Per-scenario detail",
            "headers": [
                "Scenario",
                "Hybrid NDCG@5", "TF-IDF NDCG@5",
                "Hybrid P@3", "TF-IDF P@3",
                "Hybrid RR", "TF-IDF RR",
            ],
            "rows": [
                [
                    r["scenario_id"],
                    fmt(r["hybrid"]["ndcg5"]),
                    fmt(r["tfidf"]["ndcg5"]),
                    fmt(r["hybrid"]["p3"]),
                    fmt(r["tfidf"]["p3"]),
                    fmt(r["hybrid"]["rr"]),
                    fmt(r["tfidf"]["rr"]),
                ]
                for r in rows
            ],
        }
    )

    result.notes.append(
        "Precision@K uses the classic fixed-K denominator; with fewer than K relevant "
        "jobs per scenario the attainable ceiling is below 1.0 for both systems equally."
    )
    result.notes.append(
        "NDCG uses the full 0-3 graded annotations with (2^grade - 1) gain and log2 "
        "position discounting; it separates orderings that binarized P@K cannot (e.g. a "
        "keyword-trap job ranked 2nd vs last when both systems still place the relevant "
        "jobs inside the top 3)."
    )
    result.notes.append(
        "Reference target for improvement over the keyword baseline: +35% "
        "(measure-and-report, not a pass/fail gate)."
    )
    if not cfg.no_cache:
        result.notes.append(
            "Hybrid scores include one LLM semantic-equivalence + one explanation call per "
            "pair, frozen at their first successful outcome by the eval cache — re-runs are "
            "reproducible by construction; use --no-cache to measure run-to-run variance."
        )
    result.data = {"rows": rows, "hybrid": hybrid, "tfidf": tfidf, "improvement_pct": improvements}
    return result
