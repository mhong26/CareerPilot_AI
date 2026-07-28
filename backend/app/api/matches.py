"""Match routes: run（批次計分 + explanation）/ list（FR-19~23）。"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.ai.llm.base import LLMProvider
from app.ai.llm.gemini import build_gemini_provider
from app.ai.parsers.match_schema import MatchExplanation
from app.api.deps import get_current_user
from app.db.models import Job, MatchResult, User
from app.db.session import get_db
from app.schemas.match import (
    MatchBreakdown,
    MatchResultItem,
    MatchRunRequest,
    MatchRunResponse,
    MatchSkipped,
)
from app.services import match_service
from app.services.match_service import MatchResumeNotReadyError
from app.services.resume_service import ResumeNotFoundError

router = APIRouter(prefix="/matches", tags=["matches"])


def get_llm_provider() -> LLMProvider:
    """Provider 注入點——測試可用 ``app.dependency_overrides`` 換成假 provider。"""
    return build_gemini_provider()


def _to_item(match: MatchResult, job: Job) -> MatchResultItem:
    """組裝單筆回應；JSONB 明確轉回 Pydantic（同 jobs router 慣例）。"""
    return MatchResultItem(
        id=match.id,
        job_id=match.job_id,
        job_title=job.title,
        job_company=job.company,
        match_score=match.match_score,
        breakdown=MatchBreakdown.model_validate(match.breakdown),
        explanation=(
            MatchExplanation.model_validate(match.explanation)
            if match.explanation is not None
            else None
        ),
        explanation_error=match.explanation_error,
        created_at=match.created_at,
        updated_at=match.updated_at,
    )


@router.post("/run", response_model=MatchRunResponse)
def run_matches_endpoint(
    data: MatchRunRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    provider: LLMProvider = Depends(get_llm_provider),
) -> MatchRunResponse:
    """批次計算並保存匹配（FR-19/23）。

    回 200 而非 201：這是 compute-and-upsert 動作端點，重跑覆蓋既有結果。
    """
    try:
        version, results, skipped = match_service.run_matches(
            db,
            user=current_user,
            resume_id=data.resume_id,
            job_ids=data.job_ids,
            provider=provider,
        )
    except ResumeNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Resume not found."
        ) from exc
    except MatchResumeNotReadyError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Resume has no parsed version to match against.",
        ) from exc

    return MatchRunResponse(
        resume_id=data.resume_id,
        resume_version_number=version.version_number,
        results=[_to_item(match, job) for match, job in results],
        skipped=[MatchSkipped(job_id=job_id, reason=reason) for job_id, reason in skipped],
    )


@router.get("", response_model=list[MatchResultItem])
def list_matches_endpoint(
    resume_id: uuid.UUID = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[MatchResultItem]:
    """該履歷的匹配結果，按 match_score desc（FR-21/23）。"""
    try:
        results = match_service.list_matches(db, user=current_user, resume_id=resume_id)
    except ResumeNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Resume not found."
        ) from exc
    return [_to_item(match, job) for match, job in results]
