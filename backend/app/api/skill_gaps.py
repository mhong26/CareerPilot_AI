"""Skill gap routes（FR-24~30）：run（檢索 + rerank + 生成 + upsert）/ 讀取。

Router 無 prefix：兩類 endpoint 分屬 ``/jobs/...``（以職缺為入口的動作與
pair 查詢）與 ``/skill-gaps/...``（以報告為入口的讀取），寫完整路徑。
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.ai.llm.base import LLMProvider
from app.ai.rag.rerank import Reranker
from app.api.deps import get_current_user, get_llm_provider, get_reranker
from app.core.config import settings
from app.core.ratelimit import limiter
from app.db.models import ResumeVersion, SkillGapReport, User
from app.db.session import get_db
from app.schemas.skill_gap import (
    SkillGapChunk,
    SkillGapPayload,
    SkillGapReportResponse,
    SkillGapRetrieval,
    SkillGapRunRequest,
)
from app.services import skill_gap_service
from app.services.job_service import JobNotFoundError
from app.services.resume_service import ResumeNotFoundError
from app.services.skill_gap_service import (
    SkillGapJobNotIndexedError,
    SkillGapReportNotFoundError,
    SkillGapResumeEmbeddingMissingError,
    SkillGapResumeNotReadyError,
)

router = APIRouter(tags=["skill-gaps"])


def _to_response(db: Session, report: SkillGapReport) -> SkillGapReportResponse:
    """組裝回應；JSONB 明確轉回 Pydantic（同 matches router 慣例），附引用
    chunk 原文（rerank 後順序）與履歷版本號。"""
    version = db.get(ResumeVersion, report.resume_version_id)
    chunks = skill_gap_service.load_report_chunks(db, report=report)
    return SkillGapReportResponse(
        id=report.id,
        resume_id=report.resume_id,
        resume_version_number=version.version_number if version is not None else 0,
        job_id=report.job_id,
        retrieval=SkillGapRetrieval.model_validate(report.retrieval),
        analysis=(
            SkillGapPayload.model_validate(report.analysis) if report.analysis is not None else None
        ),
        generation_error=report.generation_error,
        chunks=[SkillGapChunk(id=c.id, section=c.section, content=c.content) for c in chunks],
        created_at=report.created_at,
        updated_at=report.updated_at,
    )


@router.post("/jobs/{job_id}/skill-gap", response_model=SkillGapReportResponse)
@limiter.limit(settings.rate_limit_skill_gap)
def run_skill_gap_endpoint(
    request: Request,
    job_id: uuid.UUID,
    data: SkillGapRunRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    provider: LLMProvider = Depends(get_llm_provider),
    reranker: Reranker = Depends(get_reranker),
) -> SkillGapReportResponse:
    """執行 skill gap 分析並保存（FR-24）。

    回 200 而非 201：compute-and-upsert 動作端點，重跑覆蓋既有報告。
    """
    try:
        report, _version, _chunks = skill_gap_service.run_skill_gap(
            db,
            user=current_user,
            resume_id=data.resume_id,
            job_id=job_id,
            provider=provider,
            reranker=reranker,
        )
    except ResumeNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Resume not found."
        ) from exc
    except JobNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.") from exc
    except SkillGapResumeNotReadyError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Resume has no parsed version to analyze.",
        ) from exc
    except SkillGapResumeEmbeddingMissingError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Resume embeddings are unavailable for retrieval.",
        ) from exc
    except SkillGapJobNotIndexedError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Job is not indexed for retrieval. Re-add the job to rebuild its index.",
        ) from exc
    return _to_response(db, report)


@router.get("/jobs/{job_id}/skill-gap", response_model=SkillGapReportResponse)
def get_skill_gap_for_job_endpoint(
    job_id: uuid.UUID,
    resume_id: uuid.UUID = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SkillGapReportResponse:
    """該 (resume, job) 的既有報告（FR-24；404 = 尚未分析）。

    前端進 Job Detail 頁只知道 job_id，pair 查詢免去前端保存 report id；
    ``GET /skill-gaps/{id}`` 依 plan 另行保留。
    """
    try:
        report = skill_gap_service.get_report_for_pair(
            db, user=current_user, resume_id=resume_id, job_id=job_id
        )
    except ResumeNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Resume not found."
        ) from exc
    except JobNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.") from exc
    except SkillGapReportNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Skill gap report not found."
        ) from exc
    return _to_response(db, report)


@router.get("/skill-gaps/{report_id}", response_model=SkillGapReportResponse)
def get_skill_gap_endpoint(
    report_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SkillGapReportResponse:
    """按報告 id 讀取（FR-24 持久化查閱）。"""
    try:
        report = skill_gap_service.get_report(db, user=current_user, report_id=report_id)
    except SkillGapReportNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Skill gap report not found."
        ) from exc
    return _to_response(db, report)
