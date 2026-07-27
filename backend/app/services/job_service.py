"""職缺 ingestion / 解析 / 切塊 / 向量索引 service（FR-13~18, FR-65）。

把「原文 → 結構化職缺 → section chunks → embeddings → 寫 Job/JobChunk/JobEmbedding」
封裝成函式，router 只負責 HTTP 轉接。沿用 resume_service 的慣例與容錯策略：解析或
embedding 失敗都不整體 crash，而是照存並在 job 上標記狀態（NFR-4）。
"""

import time
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.llm.base import LLMError, LLMProvider, StructuredOutputError, TokenUsage
from app.ai.parsers.job_schema import JobParsed
from app.ai.prompts.job import JOB_PARSE_SYSTEM, build_job_parse_prompt
from app.ai.rag.chunking import chunk_job
from app.core.config import settings
from app.db.models import Job, JobChunk, JobEmbedding, User
from app.services.llm_call_log_service import record_call


class JobNotFoundError(Exception):
    """找不到該 job，或不屬於此使用者（隔離）。"""


def parse_job_text(
    db: Session, *, raw_text: str, provider: LLMProvider, user_id: uuid.UUID | None = None
) -> tuple[JobParsed | None, str | None]:
    """呼叫 Gemini 解析職缺；計時 + 記帳。

    成功回 ``(parsed, None)``；失敗回 ``(None, error)`` 而**不丟例外**——讓上層仍能
    建立 Job 並保存 parse_error（FR-10 容錯 / NFR-4 不整體 crash）。
    """
    prompt = build_job_parse_prompt(raw_text)
    model = getattr(provider, "model", settings.gemini_model)
    start = time.perf_counter()
    try:
        parsed, usage = provider.generate_structured(prompt, JobParsed, system=JOB_PARSE_SYSTEM)
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


def embed_job_chunks(
    db: Session, *, texts: list[str], provider: LLMProvider, user_id: uuid.UUID | None = None
) -> tuple[list[list[float]] | None, str | None]:
    """一次 batch 把所有 chunk 文字轉為向量；計時 + 記帳。

    成功回 ``(vectors, None)``；失敗回 ``(None, error)`` 而**不丟例外**——讓上層仍能
    保存 chunks 並把 job 標記為 index_status='failed'。
    """
    model = getattr(provider, "embedding_model", settings.embedding_model)
    prompt = "\n\n".join(texts)
    start = time.perf_counter()
    try:
        result = provider.embed(texts, task_type="RETRIEVAL_DOCUMENT")
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
        model=model,
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


def create_job_from_text(db: Session, *, user: User, raw_text: str, provider: LLMProvider) -> Job:
    """以純文字建立 Job：解析 → 切塊 → 向量化 → 寫三張表。

    提交順序刻意：所有 LLM 呼叫（其 ``record_call`` 自帶 commit）都在 add Job 之前完成，
    此時 session 尚無 job 資料，安全（同 resume_service 的不變式）。
    """
    parsed, parse_error = parse_job_text(db, raw_text=raw_text, provider=provider, user_id=user.id)
    drafts = chunk_job(parsed) if parsed is not None else []
    if drafts:
        vectors, index_error = embed_job_chunks(
            db, texts=[d.content for d in drafts], provider=provider, user_id=user.id
        )
    else:
        vectors, index_error = None, None

    if parsed is None or not drafts:
        index_status = "skipped"
    elif vectors is None:
        index_status = "failed"
    else:
        index_status = "indexed"

    job = Job(
        user_id=user.id,
        raw_text=raw_text,
        company=(parsed.company[:255] or None) if parsed is not None else None,
        title=(parsed.title[:255] or None) if parsed is not None else None,
        parsed_data=parsed.model_dump() if parsed is not None else None,
        parse_status="parsed" if parsed is not None else "failed",
        parse_error=parse_error,
        index_status=index_status,
        index_error=index_error,
    )
    db.add(job)
    db.flush()  # 取得 job.id

    chunks = [
        JobChunk(
            job_id=job.id,
            user_id=user.id,
            chunk_index=i,
            section=draft.section,
            content=draft.content,
        )
        for i, draft in enumerate(drafts)
    ]
    if chunks:
        db.add_all(chunks)
        db.flush()  # 取得 chunk.id（embedding 需要）

    # embedding 失敗時 chunks 照存、embeddings 不寫（使用者決策）。
    if vectors is not None:
        emb_model = getattr(provider, "embedding_model", settings.embedding_model)
        db.add_all(
            [
                JobEmbedding(
                    chunk_id=chunk.id,
                    job_id=job.id,
                    user_id=user.id,
                    model=emb_model,
                    vector=vector,
                )
                for chunk, vector in zip(chunks, vectors, strict=True)
            ]
        )

    db.commit()
    db.refresh(job)
    return job


# --- 讀取 / 刪除（router 用）--------------------------------------------------


def list_jobs(db: Session, *, user: User) -> list[Job]:
    """該 user 的所有 job，最新建立在前。"""
    return list(
        db.scalars(select(Job).where(Job.user_id == user.id).order_by(Job.created_at.desc()))
    )


def get_job(db: Session, *, user: User, job_id: uuid.UUID) -> Job:
    """取單份並驗 owner；非本人或不存在皆丟 JobNotFoundError（隔離）。"""
    job = db.get(Job, job_id)
    if job is None or job.user_id != user.id:
        raise JobNotFoundError(str(job_id))
    return job


def delete_job(db: Session, *, user: User, job_id: uuid.UUID) -> None:
    """硬刪除；chunks / embeddings 由 ORM delete-orphan 與 DB CASCADE 連帶清除。"""
    job = get_job(db, user=user, job_id=job_id)
    db.delete(job)
    db.commit()
