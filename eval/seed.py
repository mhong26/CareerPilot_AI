"""情境 seeding：把 dataset 的結構化履歷 / 職缺寫入 eval DB。

原則：**略過 LLM 解析（parsed 是 dataset 凍結的輸入），但走真實的
embedding 與寫入路徑**——``generate_resume_embeddings`` 與
``create_job_from_parsed`` 都是 production 程式碼，embedding 經快取
provider，每段文字終生只付費一次。

隔離與冪等：每情境一個 user（``eval+sNN@careerpilot.local``）。重跑時
manifest 條目與 DB 狀態都有效即跳過；無效（半途失敗殘骸）則整個情境刪掉
重種。``--reseed`` 刪 user 由 FK CASCADE 帶走所有資料，而
``llm_call_logs.user_id`` 是 SET NULL——帳本保留，ER-7 統計持續累積。
"""

import json
import time
import uuid
from pathlib import Path

from app.ai.rag.chunking import chunk_job
from app.db.models import Job, JobChunk, Resume, ResumeVersion, User
from app.services import resume_service
from app.services.job_service import create_job_from_parsed
from sqlalchemy import select
from sqlalchemy.orm import Session

import eval._bootstrap  # noqa: F401  # isort: split
from eval.config import EvalConfig
from eval.datasets.schema import Scenario
from eval.harness import CachingProvider, Timings

_EVAL_EMAIL_TEMPLATE = "eval+{sid}@careerpilot.local"
# eval user 永不登入；此值不是任何密碼的 bcrypt 雜湊。
_EVAL_PASSWORD_HASH = "eval-user-no-login"


class SeedError(Exception):
    """seeding 失敗（embedding 失敗等）；訊息含情境 id 與修復提示。"""


# --- manifest -----------------------------------------------------------------


def manifest_path(cfg: EvalConfig) -> Path:
    return cfg.cache_dir / "manifest.json"


def load_manifest(cfg: EvalConfig) -> dict:
    try:
        return json.loads(manifest_path(cfg).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_manifest(cfg: EvalConfig, manifest: dict) -> None:
    path = manifest_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")


# --- 驗證與重建 ---------------------------------------------------------------


def _resume_raw_text(scenario: Scenario) -> str:
    """從結構化履歷組一份可讀原文（Resume.raw_text 供人工檢視，不進任何指標）。"""
    from app.ai.embeddings.resume_texts import build_resume_embedding_texts

    parts = [scenario.resume.basic_info.name, ""]
    parts += [text for _, text in build_resume_embedding_texts(scenario.resume)]
    return "\n\n".join(p for p in parts if p)


def _entry_is_valid(db: Session, scenario: Scenario, entry: dict | None) -> bool:
    """manifest 條目與 DB 狀態是否一致（不一致 = 半途失敗殘骸，整個重種）。"""
    if entry is None:
        return False
    try:
        resume = db.get(Resume, uuid.UUID(entry["resume_id"]))
        version = db.get(ResumeVersion, uuid.UUID(entry["version_id"]))
        if resume is None or version is None or resume.parse_status != "parsed":
            return False
        if not resume_service.get_version_embeddings(db, version_id=version.id):
            return False
        if set(entry["jobs"]) != {job.job_key for job in scenario.jobs}:
            return False
        for scenario_job in scenario.jobs:
            job_entry = entry["jobs"][scenario_job.job_key]
            job = db.get(Job, uuid.UUID(job_entry["job_id"]))
            if job is None or job.index_status != "indexed":
                return False
            if len(job_entry["chunks"]) != len(chunk_job(scenario_job.parsed)):
                return False
    except (KeyError, ValueError):
        return False
    return True


def delete_scenario_data(db: Session, scenario_id: str) -> None:
    """刪掉該情境 user（CASCADE 帶走 resume / jobs / matches / reports / artifacts）。"""
    user = db.scalar(
        select(User).where(User.email == _EVAL_EMAIL_TEMPLATE.format(sid=scenario_id))
    )
    if user is not None:
        db.delete(user)
        db.commit()


# --- seeding ------------------------------------------------------------------


def seed_scenario(
    db: Session,
    scenario: Scenario,
    provider: CachingProvider,
    timings: Timings,
) -> dict:
    """種一個情境，回傳 manifest 條目。呼叫端保證此情境目前不在 DB 中。"""
    user = User(
        email=_EVAL_EMAIL_TEMPLATE.format(sid=scenario.scenario_id),
        hashed_password=_EVAL_PASSWORD_HASH,
        full_name=f"[eval] {scenario.title}",
    )
    db.add(user)
    db.flush()

    resume = Resume(
        user_id=user.id,
        source_filename=None,
        source_type="text",
        raw_text=_resume_raw_text(scenario),
        parse_status="parsed",
        parse_error=None,
    )
    db.add(resume)
    db.flush()
    version = ResumeVersion(
        resume_id=resume.id,
        user_id=user.id,
        version_number=1,
        label="original",
        parsed_data=scenario.resume.model_dump(),
    )
    db.add(version)
    db.commit()

    # 真實 embedding 路徑（RETRIEVAL_QUERY、record_call 記帳、冪等）。
    # 服務失敗時靜默返回（NFR-4），eval 不能接受靜默——事後驗證向量存在。
    with timings.timed("resume_embed", provider):
        resume_service.generate_resume_embeddings(
            db, version=version, provider=provider, user_id=user.id
        )
    if not resume_service.get_version_embeddings(db, version_id=version.id):
        raise SeedError(
            f"{scenario.scenario_id}: resume embedding failed (check GEMINI_API_KEY / quota); "
            "re-run seeding to retry"
        )

    jobs_entry: dict[str, dict] = {}
    for scenario_job in scenario.jobs:
        with timings.timed("job_index", provider):
            job = create_job_from_parsed(
                db,
                user=user,
                raw_text=scenario_job.raw_text,
                parsed=scenario_job.parsed,
                provider=provider,
            )
        if job.index_status != "indexed":
            raise SeedError(
                f"{scenario.scenario_id}/{scenario_job.job_key}: index_status="
                f"{job.index_status!r} ({job.index_error}); re-run seeding to retry"
            )
        chunks = db.scalars(
            select(JobChunk).where(JobChunk.job_id == job.id).order_by(JobChunk.chunk_index)
        ).all()
        jobs_entry[scenario_job.job_key] = {
            "job_id": str(job.id),
            "chunks": {str(c.chunk_index): str(c.id) for c in chunks},
        }

    return {
        "user_id": str(user.id),
        "resume_id": str(resume.id),
        "version_id": str(version.id),
        "jobs": jobs_entry,
    }


def seed_scenarios(
    db: Session,
    scenarios: list[Scenario],
    provider: CachingProvider,
    timings: Timings,
    cfg: EvalConfig,
    *,
    log=print,
) -> dict:
    """冪等 seeding 主入口；回傳完整 manifest（含既有有效條目）。"""
    manifest = load_manifest(cfg)

    if cfg.reseed is not None:
        targets = set(cfg.reseed) or {s.scenario_id for s in scenarios}
        for sid in sorted(targets):
            log(f"[seed] reseed: dropping {sid}")
            delete_scenario_data(db, sid)
            manifest.pop(sid, None)
        save_manifest(cfg, manifest)

    failed: list[str] = []
    for scenario in scenarios:
        sid = scenario.scenario_id
        if _entry_is_valid(db, scenario, manifest.get(sid)):
            continue
        # 無效殘骸（或首次）：清乾淨重種。
        delete_scenario_data(db, sid)
        manifest.pop(sid, None)
        log(f"[seed] seeding {sid} ({scenario.title})")
        try:
            manifest[sid] = seed_scenario(db, scenario, provider, timings)
        except SeedError as exc:
            # 單一情境失敗（多半是額度 429）不炸整輪：跳過續種其餘情境，
            # 已成功的 embed 都在快取裡，重跑只補缺的。
            db.rollback()
            failed.append(sid)
            log(f"[seed] FAILED {sid}: {exc}")
            if "429" in str(exc):
                # 分鐘配額已打穿：不等滿一個配額窗就續種，後面每個情境都會
                # 在同一窗內秒敗（tenacity 只等幾秒），整輪連鎖報銷。
                log("[seed] 429 quota hit — sleeping 65s for the per-minute window to reset")
                time.sleep(65)
            continue
        save_manifest(cfg, manifest)

    if failed:
        log(
            f"[seed] {len(failed)} scenario(s) failed to seed ({', '.join(failed)}); "
            "suites will run on the seeded subset — re-run later to retry"
        )
    return manifest


# --- suite 端的解析輔助 --------------------------------------------------------


def load_user(db: Session, entry: dict) -> User:
    user = db.get(User, uuid.UUID(entry["user_id"]))
    if user is None:
        raise SeedError("manifest points at a missing user — re-run seeding")
    return user


def job_id_to_key(entry: dict) -> dict[uuid.UUID, str]:
    return {uuid.UUID(j["job_id"]): key for key, j in entry["jobs"].items()}


def chunk_ids_for(entry: dict, job_key: str, chunk_indexes: list[int]) -> set[str]:
    """把 dataset 的 chunk_index 標註換成 DB chunk UUID 字串集合。"""
    chunks = entry["jobs"][job_key]["chunks"]
    return {chunks[str(i)] for i in chunk_indexes}
