"""Job routes: create (paste text) / list / detail / delete（FR-13~18）。"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.ai.llm.base import LLMProvider
from app.ai.llm.gemini import build_gemini_provider
from app.ai.parsers.job_schema import JobParsed
from app.ai.parsers.text_extract import TextExtractionError, extract_plain_text
from app.api.deps import get_current_user
from app.db.models import Job, User
from app.db.session import get_db
from app.schemas.job import JobCreate, JobListItem, JobResponse
from app.services import job_service
from app.services.job_service import JobNotFoundError

router = APIRouter(prefix="/jobs", tags=["jobs"])


def get_llm_provider() -> LLMProvider:
    """Provider 注入點——測試可用 ``app.dependency_overrides`` 換成假 provider。"""
    return build_gemini_provider()


def _to_response(job: Job) -> JobResponse:
    """組裝 Job 主回應；parsed_data 由 JSONB dict 明確轉回 Pydantic（型別友善）。"""
    return JobResponse(
        id=job.id,
        company=job.company,
        title=job.title,
        parse_status=job.parse_status,
        parse_error=job.parse_error,
        index_status=job.index_status,
        index_error=job.index_error,
        created_at=job.created_at,
        raw_text=job.raw_text,
        chunk_count=len(job.chunks),
        parsed_data=(
            JobParsed.model_validate(job.parsed_data) if job.parsed_data is not None else None
        ),
    )


@router.post("", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
def create_job(
    data: JobCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    provider: LLMProvider = Depends(get_llm_provider),
) -> JobResponse:
    try:
        raw_text, _ = extract_plain_text(data.raw_text)
    except TextExtractionError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    job = job_service.create_job_from_text(
        db, user=current_user, raw_text=raw_text, provider=provider
    )
    return _to_response(job)


@router.get("", response_model=list[JobListItem])
def list_jobs_endpoint(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[Job]:
    return job_service.list_jobs(db, user=current_user)


@router.get("/{job_id}", response_model=JobResponse)
def get_job_endpoint(
    job_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> JobResponse:
    try:
        job = job_service.get_job(db, user=current_user, job_id=job_id)
    except JobNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.") from exc
    return _to_response(job)


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_job_endpoint(
    job_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    try:
        job_service.delete_job(db, user=current_user, job_id=job_id)
    except JobNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.") from exc
