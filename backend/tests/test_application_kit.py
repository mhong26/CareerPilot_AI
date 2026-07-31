"""Application kit 生成函式測試（Step 5 驗收）——真 DB 記帳、假 provider。

Phase 7 Step 10 會在此擴充 agent / API 整合測試（scripted planner 四情境）。
"""

import pytest
from sqlalchemy import select

from app.ai.llm.base import (
    LLMProvider,
    StructuredOutputError,
    StructuredResult,
    TokenUsage,
)
from app.ai.parsers.kit_schema import (
    CoverLetterDraft,
    InterviewPrepSet,
    TailoredResumeSuggestions,
)
from app.ai.prompts.kit import (
    COVER_LETTER_SYSTEM,
    INTERVIEW_QS_SYSTEM,
    TAILORED_RESUME_SYSTEM,
)
from app.db.models import LLMCallLog
from app.services.application_kit_service import (
    generate_cover_letter_payload,
    generate_interview_prep_payload,
    generate_tailored_resume_payload,
)


class _FakeProvider(LLMProvider):
    """回固定 payload 或丟錯的假 provider；同時記錄收到的 schema/system 供斷言。"""

    def __init__(self, *, payload=None, error=None):
        self._payload = payload
        self._error = error
        self.model = "fake-model"
        self.seen_schema = None
        self.seen_system = None

    def generate(self, prompt, *, system=None, temperature=0.7):  # pragma: no cover
        raise NotImplementedError

    def generate_structured(self, prompt, schema, *, system=None):
        self.seen_schema = schema
        self.seen_system = system
        if self._error is not None:
            raise self._error
        return StructuredResult(
            data=self._payload,
            model=self.model,
            usage=TokenUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30),
        )

    def embed(self, texts, *, task_type=None):  # pragma: no cover
        raise NotImplementedError


def _logs(db_session):
    return db_session.execute(select(LLMCallLog)).scalars().all()


@pytest.mark.parametrize(
    ("func", "payload", "expected_schema", "expected_system"),
    [
        (
            generate_tailored_resume_payload,
            TailoredResumeSuggestions(overall_strategy="Lead with backend work."),
            TailoredResumeSuggestions,
            TAILORED_RESUME_SYSTEM,
        ),
        (
            generate_cover_letter_payload,
            CoverLetterDraft(intro="Dear team,"),
            CoverLetterDraft,
            COVER_LETTER_SYSTEM,
        ),
        (
            generate_interview_prep_payload,
            InterviewPrepSet(),
            InterviewPrepSet,
            INTERVIEW_QS_SYSTEM,
        ),
    ],
)
def test_generate_payload_success_logs_call(
    db_session, func, payload, expected_schema, expected_system
):
    """成功：回 payload、綁對 schema/system、LLMCallLog 記一筆 success。"""
    provider = _FakeProvider(payload=payload)
    data, error = func(db_session, prompt="the prompt", provider=provider)

    assert error is None
    assert data is payload
    assert provider.seen_schema is expected_schema
    assert provider.seen_system == expected_system

    logs = _logs(db_session)
    assert len(logs) == 1
    log = logs[0]
    assert log.status == "success"
    assert log.operation == "generate_structured"
    assert log.model == "fake-model"
    assert log.tokens_in == 10
    assert log.tokens_out == 20


def test_generate_payload_failure_degrades(db_session):
    """失敗：回 (None, error) 不丟例外，LLMCallLog 記一筆 error（NFR-4、FR-65）。"""
    provider = _FakeProvider(error=StructuredOutputError("cover letter gen down"))
    data, error = generate_cover_letter_payload(db_session, prompt="the prompt", provider=provider)

    assert data is None
    assert "cover letter gen down" in error

    logs = _logs(db_session)
    assert len(logs) == 1
    assert logs[0].status == "error"
    assert "cover letter gen down" in logs[0].error
