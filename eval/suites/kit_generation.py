"""Kit-generation suite：產生 tailored-resume artifacts（rubric 評分的受測物）。

走 ``application_kit_service.generate_tailored_resume_payload``——與 Phase 7
agent 的 ``generate_tailored_resume`` 工具**同一個 prompt builder 與 schema**，
受測產物品質完全等價，但省去 planner 呼叫與 LangGraph 非決定性（agent 編排
正確性已由 Phase 7 測試覆蓋；本 suite 量的是產物品質與 kit 生成延遲）。

Prompt 素材全部重用既有結果：證據 chunks 取自 rag suite 已持久化的
SkillGapReport（``load_report_chunks``）、missing skills 取自 matching suite
寫入的 MatchResult breakdown、gap hints 取自報告 analysis——零額外檢索 /
embedding 成本。產出的 payload 連同 judge 所需上下文存到
``eval/.cache/artifacts/<sid>.json`` 供 rubric suite 使用。
"""

import json
import uuid
from datetime import date

# 私名 import 是刻意的：rubric 受測物必須與 agent 工具用同一個 sections 組裝。
from app.ai.agents.tools import _resume_sections
from app.ai.prompts.kit import build_tailored_resume_prompt
from app.db.models import MatchResult
from app.services import match_scoring
from app.services.application_kit_service import generate_tailored_resume_payload
from app.services.skill_gap_service import (
    SkillGapReportNotFoundError,
    get_report_for_pair,
    load_report_chunks,
)
from sqlalchemy import select

import eval._bootstrap  # noqa: F401  # isort: split
from eval import seed as seed_mod
from eval.config import EvalConfig
from eval.datasets.schema import Scenario
from eval.harness import Providers, Timings
from eval.suites import SuiteResult

SUITE = "kit_generation"


def artifact_path(cfg: EvalConfig, scenario_id: str):
    return cfg.cache_dir / "artifacts" / f"{scenario_id}.json"


def run(
    session_maker,
    cfg: EvalConfig,
    manifest: dict,
    scenarios: list[Scenario],
    providers: Providers,
    timings: Timings,
) -> SuiteResult:
    result = SuiteResult(name=SUITE)
    generated, reused, failed = 0, 0, 0

    todo = scenarios[: cfg.limit] if cfg.limit else scenarios
    for scenario in todo:
        sid = scenario.scenario_id
        entry = manifest[sid]
        out_path = artifact_path(cfg, sid)
        if out_path.exists() and not cfg.no_cache:
            reused += 1
            continue

        gap = scenario.skill_gap
        with session_maker() as db:
            user = seed_mod.load_user(db, entry)
            resume_id = uuid.UUID(entry["resume_id"])
            job_id = uuid.UUID(entry["jobs"][gap.job_key]["job_id"])
            try:
                report = get_report_for_pair(db, user=user, resume_id=resume_id, job_id=job_id)
            except SkillGapReportNotFoundError:
                result.notes.append(f"{sid}: no skill-gap report yet — run the rag suite first")
                result.partial = True
                failed += 1
                continue
            evidence = [(c.section, c.content) for c in load_report_chunks(db, report=report)]
            gap_hints = [
                g.get("skill", "")
                for g in (report.analysis or {}).get("gaps", [])
                if g.get("skill")
            ]
            match_row = db.scalar(
                select(MatchResult).where(
                    MatchResult.resume_id == resume_id, MatchResult.job_id == job_id
                )
            )
            breakdown = (match_row.breakdown or {}) if match_row is not None else {}
            missing = list(breakdown.get("missing_required") or []) + list(
                breakdown.get("missing_preferred") or []
            )

            parsed = scenario.resume
            gap_job = next(j for j in scenario.jobs if j.job_key == gap.job_key)
            prompt = build_tailored_resume_prompt(
                job_title=gap_job.parsed.title,
                job_company=gap_job.parsed.company,
                resume_skills=match_scoring.resume_skill_pool(parsed),
                resume_summary=parsed.summary,
                resume_years=match_scoring.total_experience_years(
                    parsed.experience, now=date.today()
                ),
                resume_sections=_resume_sections(parsed),
                evidence_chunks=evidence,
                missing_skills=missing,
                gap_hints=gap_hints,
            )
            with timings.timed("kit_generate", providers.gen):
                payload, error = generate_tailored_resume_payload(
                    db, prompt=prompt, provider=providers.gen, user_id=user.id
                )

        if payload is None:
            result.notes.append(f"{sid}: tailored-resume generation failed ({error})")
            result.partial = True
            failed += 1
            continue

        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(
                {
                    "scenario_id": sid,
                    "payload": payload.model_dump(),
                    # rubric judge 所需上下文：與生成 prompt 同源，judge 不需 DB。
                    "context": {
                        "job_title": gap_job.parsed.title,
                        "job_company": gap_job.parsed.company,
                        "job_required_skills": gap_job.parsed.required_skills,
                        "job_preferred_skills": gap_job.parsed.preferred_skills,
                        "resume_summary": parsed.summary,
                        "resume_skills": match_scoring.resume_skill_pool(parsed),
                        "missing_skills": missing,
                        "gap_hints": gap_hints,
                        "evidence": [content for _section, content in evidence],
                    },
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        generated += 1

    result.tables.append(
        {
            "title": "Tailored-resume artifact generation (rubric inputs)",
            "headers": ["Generated this run", "Reused from cache", "Failed"],
            "rows": [[generated, reused, failed]],
        }
    )
    result.notes.append(
        "Artifacts are produced via the same prompt builder and schema as the Phase-7 "
        "agent's generate_tailored_resume tool; agent orchestration itself is covered by "
        "the backend test suite, so rubric scoring targets artifact quality in isolation."
    )
    result.data = {"generated": generated, "reused": reused, "failed": failed}
    return result
