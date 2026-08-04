"""履歷 ingestion / 解析 / 版本 / 向量 service（FR-7~12, FR-20, FR-56）。

職責：把「純文字 → 結構化履歷 → 寫入 Resume + 版本 → 產生 section 向量」
這條鏈路封裝成函式，router 只負責 HTTP 轉接。沿用專案慣例：函式 +
``db: Session`` 第一參數 + 自訂例外。

向量（ResumeEmbedding）於解析 / 編輯完成後產生（Phase 5 前置）：失敗不影響
儲存（NFR-4），match 執行時會 lazy backfill 再試一次。
"""

import time
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.embeddings.resume_texts import build_resume_embedding_texts
from app.ai.llm.base import LLMError, LLMProvider, StructuredOutputError, TokenUsage
from app.ai.parsers.resume_schema import ResumeParsed
from app.ai.parsers.text_extract import extract_text
from app.ai.prompts.resume import RESUME_PARSE_SYSTEM, build_resume_parse_prompt
from app.core.config import settings
from app.db.models import Resume, ResumeEmbedding, ResumeVersion, User
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
    start = time.perf_counter()
    try:
        result = provider.generate_structured(prompt, ResumeParsed, system=RESUME_PARSE_SYSTEM)
    except (StructuredOutputError, LLMError) as exc:
        # 失敗路徑的記帳 metadata 掛在例外物件上（FR-59）；getattr 防禦：
        # 測試的假例外可能沒帶完整屬性，實際模型退回 provider 設定值。
        record_call(
            db,
            provider="gemini",
            model=getattr(exc, "model", "") or getattr(provider, "model", settings.gemini_model),
            operation="generate_structured",
            prompt=prompt,
            usage=getattr(exc, "usage", None) or TokenUsage(),
            latency_ms=int((time.perf_counter() - start) * 1000),
            status="error",
            error=str(exc),
            user_id=user_id,
            attempts=getattr(exc, "attempts", 1),
            repair_used=getattr(exc, "repair_used", False),
            fallback_used=getattr(exc, "fallback_used", False),
            cost=getattr(exc, "cost_estimate", None),
        )
        return None, str(exc)

    # model / usage / cost 一律取自回傳值：fallback 可能觸發，實際用了哪個模型、
    # 花了多少（含失敗嘗試）只有 provider 知道。
    record_call(
        db,
        provider="gemini",
        model=result.model,
        operation="generate_structured",
        prompt=prompt,
        usage=result.usage,
        latency_ms=int((time.perf_counter() - start) * 1000),
        status="success",
        user_id=user_id,
        attempts=result.attempts,
        repair_used=result.repair_used,
        fallback_used=result.fallback_used,
        cost=result.cost_estimate,
    )
    return result.data, None


def embed_resume_texts(
    db: Session, *, texts: list[str], provider: LLMProvider, user_id: uuid.UUID | None = None
) -> tuple[list[list[float]] | None, str | None]:
    """一次 batch 把履歷 section 文字轉為向量；計時 + 記帳（同 ``embed_job_chunks``）。

    查詢側用 ``task_type="RETRIEVAL_QUERY"``：Gemini embedding 為非對稱設計，
    履歷（查詢側）與 job chunks（文件側，RETRIEVAL_DOCUMENT）各用各的 task_type
    才會投影到對齊的檢索空間。失敗回 ``(None, error)`` 而**不丟例外**。
    """
    model = getattr(provider, "embedding_model", settings.embedding_model)
    prompt = "\n\n".join(texts)
    start = time.perf_counter()
    try:
        result = provider.embed(texts, task_type="RETRIEVAL_QUERY")
    except LLMError as exc:
        record_call(
            db,
            provider="gemini",
            model=model,
            operation="embed",
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
        # model 取自回傳值（同 generate_structured 的記帳原則）：實際供向量的
        # 來源只有 provider 知道——eval 的快取層以此標記 cache hit。
        model=result.model,
        operation="embed",
        prompt=prompt,
        usage=result.usage,  # Gemini embedding API 不回 token 數，usage 記 0（已知限制）。
        latency_ms=int((time.perf_counter() - start) * 1000),
        status="success",
        user_id=user_id,
    )

    # 防禦：數量 / 維度不符時當作失敗，避免 flush 時 pgvector 丟 ValueError 變成 500。
    vectors = result.vectors
    if len(vectors) != len(texts) or any(len(v) != settings.embedding_dim for v in vectors):
        return None, "embedding 回傳的數量或維度與輸入不符"
    return vectors, None


def generate_resume_embeddings(
    db: Session, *, version: ResumeVersion, provider: LLMProvider, user_id: uuid.UUID
) -> None:
    """為一個履歷版本產生 section 向量（summary / skills / experience；Phase 5 前置）。

    冪等：已存在的 kind 跳過。失敗時靜默返回（``record_call`` 已留痕、儲存流程
    不受影響——與 job index 失敗同一容錯策略，NFR-4）；match 執行時會 lazy
    backfill 再試。**須在履歷 / 版本 commit 之後呼叫**（embed 的 ``record_call``
    自帶 commit，不可夾在半成品實體中間）。
    """
    parsed = ResumeParsed.model_validate(version.parsed_data)
    texts = build_resume_embedding_texts(parsed)
    if not texts:
        return
    existing = set(
        db.scalars(
            select(ResumeEmbedding.kind).where(ResumeEmbedding.resume_version_id == version.id)
        )
    )
    todo = [(kind, text) for kind, text in texts if kind not in existing]
    if not todo:
        return

    vectors, _error = embed_resume_texts(
        db, texts=[text for _, text in todo], provider=provider, user_id=user_id
    )
    if vectors is None:
        return

    model = getattr(provider, "embedding_model", settings.embedding_model)
    db.add_all(
        [
            ResumeEmbedding(
                resume_version_id=version.id,
                resume_id=version.resume_id,
                user_id=user_id,
                kind=kind,
                model=model,
                vector=vector,
            )
            for (kind, _), vector in zip(todo, vectors, strict=True)
        ]
    )
    db.commit()


def get_version_embeddings(db: Session, *, version_id: uuid.UUID) -> dict[str, list[float]]:
    """該版本的 ``kind → vector`` 對照表（match service 讀取）。"""
    rows = db.scalars(
        select(ResumeEmbedding).where(ResumeEmbedding.resume_version_id == version_id)
    )
    return {row.kind: row.vector for row in rows}


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
    add Resume，安全），再 add Resume(+version) 並 commit，最後才產生 embeddings
    （其內部的 LLM 記帳 commit 不會夾到半成品實體）。
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
    version: ResumeVersion | None = None
    if parsed is not None:
        version = ResumeVersion(
            resume_id=resume.id,
            user_id=user.id,
            version_number=1,
            label="original",
            parsed_data=parsed.model_dump(),
        )
        db.add(version)
    db.commit()
    db.refresh(resume)
    if version is not None:
        generate_resume_embeddings(db, version=version, provider=provider, user_id=user.id)
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
    db: Session,
    *,
    user: User,
    resume_id: uuid.UUID,
    new_parsed: ResumeParsed,
    provider: LLMProvider,
) -> Resume:
    """手動編輯：以完整 ResumeParsed 存為新版本（snapshot, FR-11/12）。

    新版本 commit 後為其產生 embeddings（內容變了，向量必須跟著新版本重算；
    舊版向量保留，歷史不破壞）。
    """
    resume = get_resume(db, user=user, resume_id=resume_id)
    max_version = (
        db.scalar(
            select(func.max(ResumeVersion.version_number)).where(
                ResumeVersion.resume_id == resume_id
            )
        )
        or 0
    )
    version = ResumeVersion(
        resume_id=resume.id,
        user_id=user.id,
        version_number=max_version + 1,
        label="edit",
        parsed_data=new_parsed.model_dump(),
    )
    db.add(version)
    # 編輯後即有可用結構化資料；若先前解析失敗，狀態更新為 parsed。
    resume.parse_status = "parsed"
    resume.parse_error = None
    db.commit()
    db.refresh(resume)
    generate_resume_embeddings(db, version=version, provider=provider, user_id=user.id)
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
