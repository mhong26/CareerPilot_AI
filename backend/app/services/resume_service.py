"""履歷 ingestion / 解析 / 版本 service（FR-7~12, FR-65）。

職責：把「純文字 → 結構化履歷 → 寫入 Resume + 版本」這條鏈路封裝成函式，
router 只負責 HTTP 轉接。沿用專案慣例：函式 + ``db: Session`` 第一參數 + 自訂例外。
"""

import time
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.llm.base import LLMError, LLMProvider, StructuredOutputError, TokenUsage
from app.ai.parsers.resume_schema import ResumeParsed
from app.ai.parsers.text_extract import extract_text
from app.ai.prompts.resume import RESUME_PARSE_SYSTEM, build_resume_parse_prompt
from app.core.config import settings
from app.db.models import Resume, ResumeVersion, User
from app.services.llm_call_log_service import record_call


class ResumeNotFoundError(Exception):
    """找不到該 resume，或不屬於此使用者（隔離）。"""


def parse_resume_text(
    db: Session, *, raw_text: str, provider: LLMProvider, user_id: uuid.UUID | None = None
) -> tuple[ResumeParsed | None, str | None]:
    """呼叫 Gemini 解析履歷；計時 + 記帳。

    成功回 ``(parsed, None)``；失敗回 ``(None, error)`` 而**不丟例外**——讓上層仍能
    建立 Resume 並保存 parse_error（FR-10 容錯 / NFR-4 不整體 crash）。
    """
    prompt = build_resume_parse_prompt(raw_text)
    model = getattr(provider, "model", settings.gemini_model)
    start = time.perf_counter()
    try:
        parsed, usage = provider.generate_structured(
            prompt, ResumeParsed, system=RESUME_PARSE_SYSTEM
        )
    except (StructuredOutputError, LLMError) as exc:
        record_call(
            db,
            provider="gemini",
            model=model,
            operation="generate_structured",
            prompt=prompt,
            usage=TokenUsage(),
            latency_ms=int((time.perf_counter() - start) * 1000),
            status="error",
            error=str(exc),
            user_id=user_id,
        )
        return None, str(exc)

    record_call(
        db,
        provider="gemini",
        model=model,
        operation="generate_structured",
        prompt=prompt,
        usage=usage,
        latency_ms=int((time.perf_counter() - start) * 1000),
        status="success",
        user_id=user_id,
    )
    return parsed, None


def create_resume_from_text(
    db: Session,
    *,
    user: User,
    raw_text: str,
    source_type: str,
    source_filename: str | None,
    provider: LLMProvider,
) -> Resume:
    """以已抽取的純文字建立 Resume；解析成功則同時建初版（v1, label='original'）。

    提交順序刻意：先 ``parse_resume_text``（其 ``record_call`` 自帶 commit，此時尚未
    add Resume，安全），再 add Resume(+version) 並 commit。
    """
    parsed, error = parse_resume_text(db, raw_text=raw_text, provider=provider, user_id=user.id)
    resume = Resume(
        user_id=user.id,
        source_filename=source_filename,
        source_type=source_type,
        raw_text=raw_text,
        parse_status="parsed" if parsed is not None else "failed",
        parse_error=error,
    )
    db.add(resume)
    db.flush()  # 取得 resume.id
    if parsed is not None:
        db.add(
            ResumeVersion(
                resume_id=resume.id,
                user_id=user.id,
                version_number=1,
                label="original",
                parsed_data=parsed.model_dump(),
            )
        )
    db.commit()
    db.refresh(resume)
    return resume


def create_resume_from_upload(
    db: Session,
    *,
    user: User,
    content: bytes,
    content_type: str | None,
    filename: str | None,
    provider: LLMProvider,
) -> Resume:
    """檔案上傳路徑：先抽文字（失敗往外丟、不建 Resume），再建立。"""
    raw_text, source_type = extract_text(content, content_type=content_type, filename=filename)
    return create_resume_from_text(
        db,
        user=user,
        raw_text=raw_text,
        source_type=source_type,
        source_filename=filename,
        provider=provider,
    )


def update_resume(
    db: Session, *, user: User, resume_id: uuid.UUID, new_parsed: ResumeParsed
) -> Resume:
    """手動編輯：以完整 ResumeParsed 存為新版本（snapshot, FR-11/12）。"""
    resume = get_resume(db, user=user, resume_id=resume_id)
    max_version = (
        db.scalar(
            select(func.max(ResumeVersion.version_number)).where(
                ResumeVersion.resume_id == resume_id
            )
        )
        or 0
    )
    db.add(
        ResumeVersion(
            resume_id=resume.id,
            user_id=user.id,
            version_number=max_version + 1,
            label="edit",
            parsed_data=new_parsed.model_dump(),
        )
    )
    # 編輯後即有可用結構化資料；若先前解析失敗，狀態更新為 parsed。
    resume.parse_status = "parsed"
    resume.parse_error = None
    db.commit()
    db.refresh(resume)
    return resume


# --- 讀取（router 用）---------------------------------------------------------


def get_current_resume(db: Session, *, user: User) -> Resume | None:
    """該 user 最新建立的一份（= 目前履歷）。"""
    return db.scalar(
        select(Resume).where(Resume.user_id == user.id).order_by(Resume.created_at.desc()).limit(1)
    )


def get_resume(db: Session, *, user: User, resume_id: uuid.UUID) -> Resume:
    """取單份並驗 owner；非本人或不存在皆丟 ResumeNotFoundError（隔離）。"""
    resume = db.get(Resume, resume_id)
    if resume is None or resume.user_id != user.id:
        raise ResumeNotFoundError(str(resume_id))
    return resume


def get_current_version(db: Session, *, resume: Resume) -> ResumeVersion | None:
    """version_number 最大者（= 目前生效版本）。"""
    return db.scalar(
        select(ResumeVersion)
        .where(ResumeVersion.resume_id == resume.id)
        .order_by(ResumeVersion.version_number.desc())
        .limit(1)
    )


def list_versions(db: Session, *, user: User, resume_id: uuid.UUID) -> list[ResumeVersion]:
    """依 version_number 升序列出該 resume 的所有版本（先驗 owner）。"""
    get_resume(db, user=user, resume_id=resume_id)
    return list(
        db.scalars(
            select(ResumeVersion)
            .where(ResumeVersion.resume_id == resume_id)
            .order_by(ResumeVersion.version_number.asc())
        )
    )
