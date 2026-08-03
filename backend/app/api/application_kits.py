"""Application kit routes（FR-31~44、FR-54~56）：agent run / 讀取 / 編輯。

Router 無 prefix（同 skill_gaps 慣例）：``/jobs/...`` 是以職缺為入口的動作
與 pair 查詢，``/artifacts/...`` 是以 artifact 為入口的編輯，寫完整路徑。
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from langchain_core.language_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.ai.llm.base import LLMProvider
from app.ai.llm.gemini import build_gemini_provider
from app.ai.rag.rerank import CrossEncoderReranker, Reranker
from app.api.deps import get_current_user
from app.core.config import settings
from app.db.models import GeneratedArtifact, ResumeVersion, User
from app.db.session import get_db
from app.schemas.application_kit import (
    ApplicationKitResponse,
    ArtifactResponse,
    ArtifactUpdateRequest,
    GenerateKitRequest,
)
from app.services import application_kit_service
from app.services.application_kit_service import (
    KIND_SCHEMAS,
    ArtifactContentInvalidError,
    ArtifactNotFoundError,
    KitJobNotIndexedError,
    KitResumeNotReadyError,
)
from app.services.job_service import JobNotFoundError
from app.services.resume_service import ResumeNotFoundError

router = APIRouter(tags=["application-kit"])


def get_llm_provider() -> LLMProvider:
    """Provider 注入點（工具內生成用）——測試以 ``app.dependency_overrides`` 換假。"""
    return build_gemini_provider()


def get_reranker() -> Reranker:
    """Reranker 注入點——測試換假 reranker，CI 因此永不下載 cross-encoder 模型。"""
    return CrossEncoderReranker()


def get_planner_model() -> BaseChatModel:
    """Planner 注入點——真跑用 Gemini function calling，測試換 ScriptedPlanner。

    temperature=0：planner 做的是工具選擇決策，要穩定不要創意（創意留給
    generate 工具內的 wrapper 呼叫）。
    """
    return ChatGoogleGenerativeAI(
        model=settings.gemini_model,
        google_api_key=settings.gemini_api_key,
        temperature=0,
    )


def _to_artifact_response(db: Session, artifact: GeneratedArtifact) -> ArtifactResponse:
    """組單一 artifact；content 過 kind schema 正規化（JSONB → typed 慣例）。"""
    schema = KIND_SCHEMAS[artifact.kind]
    version = db.get(ResumeVersion, artifact.resume_version_id)
    return ArtifactResponse(
        id=artifact.id,
        kind=artifact.kind,
        source=artifact.source,
        version_number=artifact.version_number,
        run_id=artifact.run_id,
        resume_version_number=version.version_number if version is not None else 0,
        content=schema.model_validate(artifact.content).model_dump(),
        created_at=artifact.created_at,
    )


def _to_kit_response(
    db: Session,
    *,
    job_id: uuid.UUID,
    resume_id: uuid.UUID,
    artifacts: dict[str, GeneratedArtifact],
    match_score: float | None,
    missing: list[str],
    errors: list[str],
) -> ApplicationKitResponse:
    by_kind = {kind: _to_artifact_response(db, artifact) for kind, artifact in artifacts.items()}
    return ApplicationKitResponse(
        job_id=job_id,
        resume_id=resume_id,
        match_score=match_score,
        tailored_resume=by_kind.get("tailored_resume"),
        cover_letter=by_kind.get("cover_letter"),
        interview_prep=by_kind.get("interview_prep"),
        missing=missing,
        errors=errors,
    )


@router.post("/jobs/{job_id}/generate-application-kit", response_model=ApplicationKitResponse)
def generate_application_kit_endpoint(
    job_id: uuid.UUID,
    data: GenerateKitRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    provider: LLMProvider = Depends(get_llm_provider),
    reranker: Reranker = Depends(get_reranker),
    planner_model: BaseChatModel = Depends(get_planner_model),
) -> ApplicationKitResponse:
    """跑 kit agent 並保存三類 artifacts（FR-56）。

    回 200 而非 201：compute-and-save 動作端點（同 ``/matches/run``）；partial
    成功也回 200 + ``missing``/``errors``（NFR-4）。同步執行，30~90 秒。
    """
    try:
        outcome = application_kit_service.run_application_kit(
            db,
            user=current_user,
            resume_id=data.resume_id,
            job_id=job_id,
            provider=provider,
            reranker=reranker,
            planner_model=planner_model,
        )
    except ResumeNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Resume not found."
        ) from exc
    except JobNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.") from exc
    except KitResumeNotReadyError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No parsed resume is available to build the application kit.",
        ) from exc
    except KitJobNotIndexedError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Job is not indexed for retrieval. Re-add the job to rebuild its index.",
        ) from exc
    return _to_kit_response(
        db,
        job_id=job_id,
        resume_id=outcome.resume.id,
        artifacts=outcome.artifacts,
        match_score=outcome.match_score,
        missing=outcome.missing,
        errors=outcome.errors,
    )


@router.get("/jobs/{job_id}/application-kit", response_model=ApplicationKitResponse)
def get_application_kit_endpoint(
    job_id: uuid.UUID,
    resume_id: uuid.UUID = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApplicationKitResponse:
    """該 (resume, job) 各類 artifact 的最新版（FR-54；404 = 尚未生成過）。"""
    try:
        resume, job, artifacts, match_score = application_kit_service.get_latest_kit(
            db, user=current_user, resume_id=resume_id, job_id=job_id
        )
    except ResumeNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Resume not found."
        ) from exc
    except JobNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.") from exc
    if not artifacts:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Application kit not found."
        )
    return _to_kit_response(
        db,
        job_id=job.id,
        resume_id=resume.id,
        artifacts=artifacts,
        match_score=match_score,
        missing=[k for k in KIND_SCHEMAS if k not in artifacts],
        errors=[],
    )


@router.patch("/artifacts/{artifact_id}", response_model=ArtifactResponse)
def update_artifact_endpoint(
    artifact_id: uuid.UUID,
    data: ArtifactUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ArtifactResponse:
    """保存使用者編輯版（FR-40）——append-only 出新版本 row，非就地更新。"""
    try:
        edited = application_kit_service.update_artifact(
            db, user=current_user, artifact_id=artifact_id, content=data.content
        )
    except ArtifactNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found."
        ) from exc
    except (ArtifactContentInvalidError, ValidationError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Artifact content does not match the expected structure for its kind.",
        ) from exc
    return _to_artifact_response(db, edited)
