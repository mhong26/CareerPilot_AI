"""Hallucination suite（ER-6）：LLM-as-judge 逐 claim 檢驗 vs no-RAG baseline。

流程：
1. 本地組料——RAG 側 claims 取自 rag suite 已持久化的 SkillGapReport
   （每條 gap = 一個 claim，證據 = 其 cited chunks 原文）；no-RAG 側由
   ``generate_baseline_gaps`` 直接生成（看不到職缺內文），judge 時給它
   **該 job 全部 chunk 原文**當證據——最寬容的證據集，偏向低估 RAG 的
   改善（讓量到的差距更可信）。
2. ``langsmith.evaluate()`` 跑兩個 experiment（rag / no-rag），traces 留在
   LangSmith（ER-6 要求）；target 只回傳已備好的 claims（零 LLM），judge
   在 evaluator 內經快取呼叫——重跑不燒額度、可跨日續跑。
3. 免 judge 的補充訊號：``must_not_claim`` 標註比對（宣稱履歷明顯具備的
   技能為缺口 = 標註幻覺）與 citation 驗證丟棄數（dropped_gap_count）。

額度：judge 呼叫觸頂（429）→ 該 experiment 標 partial、停止後續 judge，
已完成的部分照報（下次重跑從快取續進度）。
"""

import re
import uuid

from app.ai.rag.chunking import chunk_job
from app.db.models import JobChunk
from app.services.skill_gap_service import SkillGapReportNotFoundError, get_report_for_pair
from sqlalchemy import select

import eval._bootstrap  # noqa: F401  # isort: split
from eval import seed as seed_mod
from eval.baselines.no_rag_gaps import generate_baseline_gaps
from eval.config import EvalConfig
from eval.datasets.schema import Scenario
from eval.harness import Providers, Timings
from eval.langsmith.evaluators import (
    JudgeCallError,
    JudgeQuotaExhausted,
    call_judge,
    judge_hallucination,
    tally_verdicts,
)
from eval.langsmith.sync_datasets import DATASET_SKILLGAP, sync_all
from eval.suites import SuiteResult

SUITE = "hallucination"


def _skill_tokens(name: str) -> tuple[str, ...]:
    return tuple(re.findall(r"[a-z0-9+#]+", name.lower()))


def _must_not_claim_hits(claim_skills: list[str], must_not_claim: list[str]) -> list[str]:
    """宣稱為缺口、但標註明示履歷具備的技能（正規化 token 序列相等）。

    刻意不用子字串比對：\"C\" 會命中一切含 c 的技能、\"Java\" 會命中
    \"JavaScript\"。token 序列完全相等（大小寫 / 標點 / 空白不敏感）才算
    同一技能——寧可漏報也不誤報（這是輔助訊號，主訊號是 judge）。
    """
    must = {t for m in must_not_claim if (t := _skill_tokens(m))}
    return [skill for skill in claim_skills if _skill_tokens(skill) in must]


def _prepare(session_maker, manifest, scenarios, providers, result) -> dict[str, dict]:
    """組出每個情境的 judge 輸入；不可評的情境記 note 後略過。"""
    prepared: dict[str, dict] = {}
    for scenario in scenarios:
        sid = scenario.scenario_id
        entry = manifest[sid]
        gap = scenario.skill_gap
        gap_job = next(j for j in scenario.jobs if j.job_key == gap.job_key)
        all_chunk_texts = [d.content for d in chunk_job(gap_job.parsed)]

        with session_maker() as db:
            user = seed_mod.load_user(db, entry)
            try:
                report = get_report_for_pair(
                    db,
                    user=user,
                    resume_id=uuid.UUID(entry["resume_id"]),
                    job_id=uuid.UUID(entry["jobs"][gap.job_key]["job_id"]),
                )
            except SkillGapReportNotFoundError:
                result.notes.append(f"{sid}: no skill-gap report — run the rag suite first")
                result.partial = True
                continue
            analysis = report.analysis
            if not analysis:
                result.notes.append(
                    f"{sid}: skill-gap generation failed ({report.generation_error}); "
                    "no claims to judge"
                )
                result.partial = True
                continue
            chunk_ids = {
                uuid.UUID(cid)
                for g in analysis.get("gaps", [])
                for cid in g.get("evidence_chunk_ids", [])
            }
            texts_by_id = {
                str(row.id): row.content
                for row in db.scalars(select(JobChunk).where(JobChunk.id.in_(chunk_ids)))
            }

        rag_claims = [
            {
                "skill": g.get("skill", ""),
                "severity": g.get("severity", "medium"),
                "reason": g.get("reason", ""),
                "evidence_texts": [
                    texts_by_id[cid] for cid in g.get("evidence_chunk_ids", [])
                    if cid in texts_by_id
                ],
            }
            for g in analysis.get("gaps", [])
        ]

        baseline, baseline_error = generate_baseline_gaps(providers.gen, scenario)
        if baseline is None:
            result.notes.append(f"{sid}: no-RAG baseline generation failed ({baseline_error})")
            result.partial = True
            norag_claims = None
        else:
            # baseline 無 citation：對「全部 chunk 原文」評（最寬容證據集）。
            norag_claims = [
                {
                    "skill": g.skill,
                    "severity": g.severity,
                    "reason": g.reason,
                    "evidence_texts": all_chunk_texts,
                }
                for g in baseline.gaps
            ]

        resume_skills = scenario.resume.skills
        prepared[sid] = {
            "resume_skills": resume_skills,
            "rag_claims": rag_claims,
            "norag_claims": norag_claims,
            "dropped_gap_count": int(analysis.get("dropped_gap_count", 0)),
            "rag_mnc_hits": _must_not_claim_hits(
                [c["skill"] for c in rag_claims], gap.must_not_claim
            ),
            "norag_mnc_hits": _must_not_claim_hits(
                [c["skill"] for c in (norag_claims or [])], gap.must_not_claim
            ),
        }
    return prepared


class _QuotaState:
    exhausted = False
    reason = ""


def _run_experiment(
    kind: str,
    prepared: dict[str, dict],
    providers: Providers,
    quota: _QuotaState,
    counts_acc: dict[str, dict],
) -> None:
    """跑一個 langsmith experiment（kind ∈ {rag, norag}）。"""
    from langsmith import evaluate

    claims_key = f"{kind}_claims"

    def target(inputs: dict) -> dict:
        material = prepared.get(inputs["scenario_id"])
        return {
            "scenario_id": inputs["scenario_id"],
            "claims": material.get(claims_key) if material else None,
            "resume_skills": material["resume_skills"] if material else [],
        }

    def evaluator(run, example) -> dict:
        key = f"hallucination_rate_{kind}"
        outputs = run.outputs or {}
        claims = outputs.get("claims")
        sid = outputs.get("scenario_id", "?")
        if claims is None:
            return {"key": key, "score": None, "comment": "not prepared in this run"}
        if not claims:
            counts_acc[sid] = {"supported": 0, "partially_supported": 0, "unsupported": 0,
                               "unjudged": 0}
            return {"key": key, "score": 0.0, "comment": "no gap claims produced"}
        if quota.exhausted:
            return {"key": key, "score": None, "comment": "judge quota exhausted this run"}
        try:
            judgment = call_judge(
                judge_hallucination,
                providers.judge,
                resume_skills=outputs.get("resume_skills", []),
                claims=claims,
            )
        except JudgeQuotaExhausted as exc:
            quota.exhausted = True
            quota.reason = str(exc)
            return {"key": key, "score": None, "comment": "judge quota exhausted"}
        except JudgeCallError as exc:
            # 單次失敗不放棄整輪；該情境本輪不計分，重跑可續。
            return {"key": key, "score": None, "comment": f"judge call failed: {exc}"[:400]}
        counts = tally_verdicts(claims, judgment)
        counts_acc[sid] = counts
        judged = len(claims) - counts["unjudged"]
        score = counts["unsupported"] / judged if judged else None
        return {"key": key, "score": score, "comment": f"{counts}"}

    evaluate(
        target,
        data=DATASET_SKILLGAP,
        evaluators=[evaluator],
        experiment_prefix=f"careerpilot-hallucination-{kind}",
        max_concurrency=1,
    )


def _aggregate(counts_acc: dict[str, dict]) -> dict:
    total = {"supported": 0, "partially_supported": 0, "unsupported": 0, "unjudged": 0}
    for counts in counts_acc.values():
        for k in total:
            total[k] += counts.get(k, 0)
    judged = total["supported"] + total["partially_supported"] + total["unsupported"]
    return {
        **total,
        "judged": judged,
        "rate": (total["unsupported"] / judged) if judged else None,
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
    todo = scenarios[: cfg.limit] if cfg.limit else scenarios
    prepared = _prepare(session_maker, manifest, todo, providers, result)
    if not prepared:
        result.skipped = True
        result.notes.append("no scenario had judgeable skill-gap output")
        return result

    # dataset 涵蓋不足時自動同步（本地 JSON 是 source of truth）：只驗
    # dataset 存在不夠——可能是被 --limit 截斷的舊版，要驗雲端 example 的
    # scenario_id 集合涵蓋本次要評的所有情境。同步一律 sync_all（完整
    # dataset）。LangSmith 完全不可用（401 / 網路）時整個 suite 降級為
    # skipped：不炸整輪、（run_eval 不落盤）不覆蓋既有成功結果。
    from langsmith import Client

    try:
        client = Client()
        try:
            cloud_ids = {
                (e.metadata or {}).get("scenario_id")
                for e in client.list_examples(dataset_name=DATASET_SKILLGAP)
            }
        except Exception:
            cloud_ids = set()
        if not set(prepared) <= cloud_ids:
            sync_all()
    except Exception as exc:
        result.skipped = True
        result.notes.append(f"LangSmith unavailable ({exc}) — judge suite not run")
        return result

    quota = _QuotaState()
    rag_counts: dict[str, dict] = {}
    norag_counts: dict[str, dict] = {}
    try:
        _run_experiment("rag", prepared, providers, quota, rag_counts)
        _run_experiment("norag", prepared, providers, quota, norag_counts)
    except Exception as exc:  # noqa: BLE001 — LangSmith 連線失敗等：如實降級
        if not rag_counts and not norag_counts:
            # 一個判定都沒收到：全零的表格會誤導，整個 suite 標 skipped。
            result.skipped = True
            result.notes.append(f"langsmith.evaluate failed before any judgment ({exc})")
            return result
        result.notes.append(f"langsmith.evaluate failed mid-run: {exc}")
        result.partial = True

    if not rag_counts and not norag_counts:
        # 一個判定都沒收到（多半是額度一開始就耗盡）：空表不得覆蓋既有結果。
        result.skipped = True
        reason = quota.reason[:120] if quota.exhausted else "no judgments returned"
        result.notes.append(f"no scenario judged this run ({reason}…)")
        return result

    rag = _aggregate(rag_counts)
    norag = _aggregate(norag_counts)
    if not quota.exhausted and len(rag_counts) < len(prepared):
        result.partial = True
        result.notes.append(
            f"{len(prepared) - len(rag_counts)} scenario(s) had a failed judge call "
            "this run (non-quota) — re-run to retry them"
        )
    if quota.exhausted:
        result.partial = True
        result.notes.append(
            f"judge quota exhausted mid-run ({quota.reason[:120]}…); judged "
            f"{len(rag_counts)}/{len(prepared)} RAG and {len(norag_counts)}/{len(prepared)} "
            "no-RAG scenarios — re-run on a later day to continue (cache resumes progress)"
        )

    def rate_cell(agg: dict) -> str:
        if agg["rate"] is None:
            return "n/a"
        return f"{agg['rate']:.1%} ({agg['unsupported']}/{agg['judged']})"

    result.tables.append(
        {
            "title": "Hallucination rate — RAG skill-gap vs no-RAG direct generation",
            "headers": ["System", "Hallucination rate (unsupported/judged)", "Supported",
                        "Partially", "Unsupported", "Unjudged"],
            "rows": [
                ["RAG (production)", rate_cell(rag), rag["supported"],
                 rag["partially_supported"], rag["unsupported"], rag["unjudged"]],
                ["No-RAG baseline", rate_cell(norag), norag["supported"],
                 norag["partially_supported"], norag["unsupported"], norag["unjudged"]],
            ],
        }
    )

    mnc_rows = []
    total_dropped = 0
    for sid, material in prepared.items():
        total_dropped += material["dropped_gap_count"]
        if material["rag_mnc_hits"] or material["norag_mnc_hits"]:
            mnc_rows.append(
                [sid, ", ".join(material["rag_mnc_hits"]) or "-",
                 ", ".join(material["norag_mnc_hits"]) or "-"]
            )
    result.tables.append(
        {
            "title": "Judge-free hallucination signals",
            "headers": ["Signal", "Value"],
            "rows": [
                ["Claims naming a skill the resume clearly has (RAG)",
                 sum(len(m["rag_mnc_hits"]) for m in prepared.values())],
                ["Claims naming a skill the resume clearly has (no-RAG)",
                 sum(len(m["norag_mnc_hits"]) for m in prepared.values())],
                ["Gaps dropped by citation validation (RAG, dropped_gap_count)", total_dropped],
            ],
        }
    )
    if mnc_rows:
        result.tables.append(
            {
                "title": "must_not_claim violations by scenario",
                "headers": ["Scenario", "RAG", "No-RAG"],
                "rows": mnc_rows,
            }
        )

    result.notes.append(
        "The no-RAG baseline never sees the posting text, but its claims are judged "
        "against the job's FULL chunk text (the most charitable evidence set) — this "
        "biases the comparison against RAG, so a measured improvement is conservative."
    )
    result.notes.append(
        "One judge call covers all claims of one report (claims batched into a single "
        "prompt) — 2 calls per scenario across both experiments."
    )
    result.data = {
        "rag": rag,
        "norag": norag,
        "prepared": len(prepared),
        "dropped_gap_count": total_dropped,
    }
    return result
