"""Resume routes: upload / parse, current, detail, edit (versioned), versions list."""

import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.ai.llm.base import LLMProvider
from app.ai.llm.gemini import build_gemini_provider
from app.ai.parsers.resume_schema import ResumeParsed
from app.ai.parsers.text_extract import (
    TextExtractionError,
    UnsupportedFileTypeError,
    extract_plain_text,
)
from app.api.deps import get_current_user
from app.core.config import settings
from app.db.models import Resume, ResumeVersion, User
from app.db.session import get_db
from app.schemas.resume import ResumeResponse, ResumeUpdate, ResumeVersionResponse
from app.services import resume_service
from app.services.resume_service import ResumeNotFoundError

router = APIRouter(prefix="/resumes", tags=["resumes"])


def get_llm_provider() -> LLMProvider:
    """Provider 注入點——測試可用 ``app.dependency_overrides`` 換成假 provider。"""
    return build_gemini_provider()


def _to_response(resume: Resume, current_version: ResumeVersion | None) -> ResumeResponse:
    """合併 Resume 欄位與目前版本的 parsed_data / version_number。"""
    return ResumeResponse(
        id=resume.id,
        source_filename=resume.source_filename,
        source_type=resume.source_type,
        parse_status=resume.parse_status,
        parse_error=resume.parse_error,
        created_at=resume.created_at,
        current_version_number=(
            current_version.version_number if current_version is not None else None
        ),
        parsed_data=(
            ResumeParsed.model_validate(current_version.parsed_data)
            if current_version is not None
            else None
        ),
    )


def _build_response(db: Session, resume: Resume) -> ResumeResponse:
    current = resume_service.get_current_version(db, resume=resume)
    return _to_response(resume, current)


@router.post("/upload", response_model=ResumeResponse, status_code=status.HTTP_201_CREATED)
def upload_resume(
    file: UploadFile | None = File(default=None),
    text_content: str | None = Form(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    provider: LLMProvider = Depends(get_llm_provider),
) -> ResumeResponse:
    has_file = file is not None
    has_text = text_content is not None and text_content.strip() != ""
    if has_file == has_text:  # 兩者都給或都不給
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide exactly one of 'file' or 'text_content'.",
        )

    try:
        if has_file:
            assert file is not None  # narrow for type-checkers
            content = file.file.read()
            limit = settings.max_upload_size_mb * 1024 * 1024
            if len(content) > limit:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"File exceeds {settings.max_upload_size_mb} MB limit.",
                )
            resume = resume_service.create_resume_from_upload(
                db,
                user=current_user,
                content=content,
                content_type=file.content_type,
                filename=file.filename,
                provider=provider,
            )
        else:
            assert text_content is not None
            raw_text, source_type = extract_plain_text(text_content)
            resume = resume_service.create_resume_from_text(
                db,
                user=current_user,
                raw_text=raw_text,
                source_type=source_type,
                source_filename=None,
                provider=provider,
            )
    except UnsupportedFileTypeError as exc:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Unsupported file type (only PDF and DOCX are accepted).",
        ) from exc
    except TextExtractionError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    return _build_response(db, resume)


@router.get("/current", response_model=ResumeResponse)
def get_current(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ResumeResponse:
    resume = resume_service.get_current_resume(db, user=current_user)
    if resume is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No resume found for this user.",
        )
    return _build_response(db, resume)


@router.get("/{resume_id}", response_model=ResumeResponse)
def get_one(
    resume_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ResumeResponse:
    try:
        resume = resume_service.get_resume(db, user=current_user, resume_id=resume_id)
    except ResumeNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Resume not found."
        ) from exc
    return _build_response(db, resume)


@router.patch("/{resume_id}", response_model=ResumeResponse)
def edit_resume(
    resume_id: uuid.UUID,
    data: ResumeUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ResumeResponse:
    try:
        resume = resume_service.update_resume(
            db, user=current_user, resume_id=resume_id, new_parsed=data.parsed_data
        )
    except ResumeNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Resume not found."
        ) from exc
    return _build_response(db, resume)


@router.get("/{resume_id}/versions", response_model=list[ResumeVersionResponse])
def list_resume_versions(
    resume_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ResumeVersion]:
    try:
        return resume_service.list_versions(db, user=current_user, resume_id=resume_id)
    except ResumeNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Resume not found."
        ) from exc
