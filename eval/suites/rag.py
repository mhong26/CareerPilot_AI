"""RAG retrieval suite（ER-5）：skill-gap 檢索的 P@K / MRR 與 rerank 前後對比。

對每個情境的 gap 目標 job 跑真實 ``run_skill_gap``（pgvector 檢索 →
cross-encoder rerank → 生成；生成結果同時餵後續 hallucination / rubric
suite）。指標直接取自持久化的 ``SkillGapReport.retrieval``：

- rerank 前 = ``retrieval["chunks"]``（向量相似度序）
- rerank 後 = ``retrieval["ranked_chunk_ids"]``

ground truth 為 dataset 的 ``skill_gap.relevant_chunks``（chunk_index 經
manifest 映成 DB chunk UUID）。
"""

import uuid

from app.ai.rag.rerank import CrossEncoderReranker
from app.services.skill_gap_service import run_skill_gap

import eval._bootstrap  # noqa: F401  # isort: split
from eval import seed as seed_mod
from eval.config import EvalConfig
from eval.datasets.schema import Scenario
from eval.harness import Providers, Timings
from eval.metrics import mean, mrr, precision_at_k, reciprocal_rank
from eval.suites import SuiteResult, fmt

SUITE = "rag"


def _metrics(ranked: list[str], relevant: set[str]) -> dict[str, float]:
    return {
        "p3": precision_at_k(ranked, relevant, 3),
        "p5": precision_at_k(ranked, relevant, 5),
        "rr": reciprocal_rank(ranked, relevant),
        "ranked": ranked,
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
    reranker = CrossEncoderReranker()

    todo = scenarios[: cfg.limit] if cfg.limit else scenarios
    for scenario in todo:
        entry = manifest[scenario.scenario_id]
        gap = scenario.skill_gap
        relevant = seed_mod.chunk_ids_for(entry, gap.job_key, gap.relevant_chunks)

        with session_maker() as db:
            user = seed_mod.load_user(db, entry)
            with timings.timed("skill_gap_run", providers.gen):
                report, _version, _ranked = run_skill_gap(
                    db,
                    user=user,
                    resume_id=uuid.UUID(entry["resume_id"]),
                    job_id=uuid.UUID(entry["jobs"][gap.job_key]["job_id"]),
                    provider=providers.gen,
                    reranker=reranker,
                )
            retrieval = report.retrieval
            generation_error = report.generation_error

        pre = [c["chunk_id"] for c in retrieval["chunks"]]
        post = list(retrieval["ranked_chunk_ids"])
        rows.append(
            {
                "scenario_id": scenario.scenario_id,
                "relevant": sorted(relevant),
                "rerank_used": bool(retrieval.get("rerank_used")),
                "pre": _metrics(pre, relevant),
                "post": _metrics(post, relevant),
                "generation_error": generation_error,
            }
        )
        if generation_error:
            result.notes.append(
                f"{scenario.scenario_id}: skill-gap generation failed ({generation_error}); "
                "retrieval metrics unaffected, hallucination suite will skip this scenario"
            )
            result.partial = True

    def macro(stage: str) -> dict[str, float]:
        return {
            "p3": mean(r[stage]["p3"] for r in rows),
            "p5": mean(r[stage]["p5"] for r in rows),
            "mrr": mrr((r[stage]["ranked"], set(r["relevant"])) for r in rows),
        }

    pre_macro, post_macro = macro("pre"), macro("post")
    rerank_share = mean(1.0 if r["rerank_used"] else 0.0 for r in rows)

    result.tables.append(
        {
            "title": f"Skill-gap retrieval — before vs after rerank (n={len(rows)} scenarios)",
            "headers": ["Metric", "Vector order (pre-rerank)", "Cross-encoder order (post)"],
            "rows": [
                ["Precision@3", fmt(pre_macro["p3"]), fmt(post_macro["p3"])],
                ["Precision@5", fmt(pre_macro["p5"]), fmt(post_macro["p5"])],
                ["MRR", fmt(pre_macro["mrr"]), fmt(post_macro["mrr"])],
            ],
        }
    )
    result.tables.append(
        {
            "title": "Per-scenario detail",
            "headers": ["Scenario", "Pre P@3", "Post P@3", "Pre RR", "Post RR", "Rerank used"],
            "rows": [
                [
                    r["scenario_id"],
                    fmt(r["pre"]["p3"]),
                    fmt(r["post"]["p3"]),
                    fmt(r["pre"]["rr"]),
                    fmt(r["post"]["rr"]),
                    "yes" if r["rerank_used"] else "no",
                ]
                for r in rows
            ],
        }
    )

    result.notes.append(
        f"Rerank applied in {rerank_share:.0%} of runs "
        f"(model: cross-encoder/ms-marco-MiniLM-L-6-v2; when unavailable the service "
        "degrades to vector order, making pre == post for that scenario)."
    )
    result.notes.append(
        "Retrieval is top-k=5 over each job's chunks; chunk-level ground truth is "
        "annotated only for the skill-gap query of each scenario (binary relevance)."
    )
    result.data = {
        "rows": rows,
        "pre": pre_macro,
        "post": post_macro,
        "rerank_share": rerank_share,
    }
    return result
