"""Demo seeding：建立可真實登入的示範帳號與資料（Phase 10）。

原則同 ``eval/seed.py``：**略過 LLM 解析（parsed 凍結在 seed_data.json），但走
真實的 embedding 與寫入路徑**——``generate_resume_embeddings`` 與
``create_job_from_parsed`` 都是 production 程式碼，chunk / 向量與正式流程完全
一致；密碼走與註冊端相同的 bcrypt 雜湊，demo 帳號可以真的登入。

冪等：demo email 已存在即印訊息跳過；``--reseed`` 先刪 demo users（FK CASCADE
帶走履歷 / 職缺 / match 等所有資料；``llm_call_logs.user_id`` 為 SET NULL，
帳本保留）再重種。只動 seed_data.json 裡的兩個 demo 帳號，永不碰其他使用者。

執行（容器內，cwd /app）：``python scripts/seed.py [--reseed]``
"""

import argparse
import json
import sys
from pathlib import Path

# 以 `python scripts/seed.py` 執行時 sys.path[0] 是 scripts/；補上上層目錄
# （容器內為 /app）才 import 得到 app.*。
_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.ai.llm.base import LLMProvider  # noqa: E402
from app.ai.llm.gemini import build_gemini_provider  # noqa: E402
from app.ai.parsers.job_schema import JobParsed  # noqa: E402
from app.ai.parsers.resume_schema import ResumeParsed  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.db.models import Resume, ResumeVersion, User  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.services import resume_service  # noqa: E402
from app.services.job_service import create_job_from_parsed  # noqa: E402

# seed 資料與本檔並排（自包含，不依賴 eval/ 或 repo root）。
_SEED_DATA_PATH = Path(__file__).resolve().parent / "seed_data.json"


class SeedError(Exception):
    """seeding 失敗（embedding 失敗等）；訊息含帳號與修復提示。"""


def delete_demo_user(db: Session, email: str) -> bool:
    """刪掉 demo user（CASCADE 帶走 resume / jobs / matches / reports / artifacts）。"""
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        return False
    db.delete(user)
    db.commit()
    return True


def seed_user(db: Session, entry: dict, provider: LLMProvider) -> None:
    """種一個 demo 帳號。呼叫端保證該 email 目前不在 DB 中。"""
    email = entry["email"]
    resume_entry = entry["resume"]
    resume_parsed = ResumeParsed.model_validate(resume_entry["parsed"])

    user = User(
        email=email,
        # 與註冊端同一 bcrypt 函式（app.core.security.hash_password），可真實登入。
        hashed_password=hash_password(entry["password"]),
        full_name=entry.get("full_name"),
    )
    db.add(user)
    db.flush()

    resume = Resume(
        user_id=user.id,
        source_filename=None,
        source_type="text",
        raw_text=resume_entry["raw_text"],
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
        parsed_data=resume_parsed.model_dump(),
    )
    db.add(version)
    db.commit()

    # 真實 embedding 路徑；服務失敗時靜默返回（NFR-4），seed 不能接受靜默——
    # 事後驗證向量存在（同 eval/seed.py）。
    resume_service.generate_resume_embeddings(
        db, version=version, provider=provider, user_id=user.id
    )
    embeddings = resume_service.get_version_embeddings(db, version_id=version.id)
    if not embeddings:
        raise SeedError(
            f"{email}: resume embedding failed (check GEMINI_API_KEY / quota); re-run to retry"
        )
    print(f"  resume embedded: {resume_parsed.basic_info.name} ({len(embeddings)} sections)")

    jobs = entry["jobs"]
    for i, job_entry in enumerate(jobs, start=1):
        job_parsed = JobParsed.model_validate(job_entry["parsed"])
        job = create_job_from_parsed(
            db, user=user, raw_text=job_entry["raw_text"], parsed=job_parsed, provider=provider
        )
        if job.index_status != "indexed":
            raise SeedError(
                f"{email}: job '{job_parsed.title} — {job_parsed.company}' index_status="
                f"{job.index_status!r} ({job.index_error}); re-run to retry"
            )
        print(f"  [{i}/{len(jobs)}] job indexed: {job_parsed.title} — {job_parsed.company}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed demo accounts with frozen parsed data.")
    parser.add_argument(
        "--reseed", action="store_true", help="delete the demo users first, then seed again"
    )
    args = parser.parse_args()

    data = json.loads(_SEED_DATA_PATH.read_text(encoding="utf-8"))
    provider = build_gemini_provider()
    db = SessionLocal()
    try:
        for entry in data["users"]:
            email = entry["email"]
            if args.reseed and delete_demo_user(db, email):
                print(f"[seed] --reseed: dropped {email}")
            if db.scalar(select(User).where(User.email == email)) is not None:
                print(f"[seed] {email} already exists — skipping (use --reseed to recreate)")
                continue
            print(f"[seed] seeding {email} ...")
            try:
                seed_user(db, entry, provider)
            except SeedError as exc:
                # 清掉半成品 user，下次重跑不需 --reseed 即可重試。
                db.rollback()
                delete_demo_user(db, email)
                print(f"[seed] FAILED: {exc}")
                return 1
            print(f"[seed] done: {email} / password: {entry['password']}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
