"""Application kit 純函式單元測試——零 fixture、不碰 DB / LLM（Step 3 / Step 4 驗收）。

涵蓋：三個 structured output schema 的 default 容錯與 Gemini schema 轉換、
三個 prompt builder 的格式／截斷／可選區塊行為。
"""

from app.ai.llm.schema_utils import to_gemini_schema
from app.ai.parsers.kit_schema import (
    CoverLetterDraft,
    InterviewPrepSet,
    TailoredResumeSuggestions,
)
from app.ai.prompts.kit import (
    build_cover_letter_prompt,
    build_interview_qs_prompt,
    build_tailored_resume_prompt,
)

# ---------- Step 3：schemas ----------


def test_kit_schemas_validate_empty_payload():
    """每欄位都有 default——空 dict 也能通過驗證（LLM 部分輸出不整包失敗）。"""
    suggestions = TailoredResumeSuggestions.model_validate({})
    assert suggestions.overall_strategy == ""
    assert suggestions.section_suggestions == []
    assert suggestions.top_keywords == []

    letter = CoverLetterDraft.model_validate({})
    assert letter.intro == ""
    assert letter.body_paragraphs == []
    assert letter.closing == ""

    prep = InterviewPrepSet.model_validate({})
    assert prep.questions == []


def test_kit_schemas_nested_defaults():
    """巢狀 item 同樣容錯：漏欄補 default。"""
    suggestions = TailoredResumeSuggestions.model_validate(
        {"section_suggestions": [{"section": "skills", "bullet_rewrites": [{}]}]}
    )
    rewrite = suggestions.section_suggestions[0].bullet_rewrites[0]
    assert rewrite.original == ""
    assert rewrite.improved == ""

    prep = InterviewPrepSet.model_validate({"questions": [{"question": "Why us?"}]})
    assert prep.questions[0].category == ""
    assert prep.questions[0].answer_outline == []


def test_kit_schemas_gemini_convertible():
    """to_gemini_schema 能轉換三個 schema（default/$defs 剝除不報錯）。"""
    for schema in (TailoredResumeSuggestions, CoverLetterDraft, InterviewPrepSet):
        converted = to_gemini_schema(schema)
        assert converted["type"] == "object"
        assert "properties" in converted


# ---------- Step 4：prompt builders ----------

_RESUME_KWARGS = {
    "resume_skills": ["Python", "SQL"],
    "resume_summary": "Backend engineer.",
    "resume_years": 3.0,
    "resume_sections": [("experience", "Acme — Engineer\n- Built APIs")],
}


def test_build_tailored_resume_prompt_format():
    """證據 [n] (section) 編號、chunk/section 雙截斷、可選區塊有無皆正確。"""
    long_chunk = "x" * 2000
    long_section = "y" * 2500
    prompt = build_tailored_resume_prompt(
        job_title="Backend Engineer",
        job_company="Acme",
        resume_skills=["Python"],
        resume_summary="Backend engineer.",
        resume_years=3.0,
        resume_sections=[("experience", long_section)],
        evidence_chunks=[("required_skills", "Required: Python, Go"), ("overview", long_chunk)],
        missing_skills=["Go"],
        gap_hints=["Kubernetes"],
    )
    assert "Job: Backend Engineer at Acme" in prompt
    assert "[1] (required_skills) Required: Python, Go" in prompt
    assert "[2] (overview) " + "x" * 1600 in prompt
    assert "x" * 1601 not in prompt  # _MAX_CHUNK_CHARS 截斷
    assert "--- experience ---\n" + "y" * 2000 in prompt
    assert "y" * 2001 not in prompt  # _MAX_SECTION_CHARS 截斷
    assert "Skills the job requires that the resume lacks: Go" in prompt
    assert "Known skill-gap hints from a prior analysis: Kubernetes" in prompt
    assert "Total experience: 3.0 years" in prompt


def test_build_tailored_resume_prompt_empty_inputs():
    """空輸入 fallback：(unknown ...)、(none listed)、可選區塊整段缺席。"""
    prompt = build_tailored_resume_prompt(
        job_title="",
        job_company="",
        resume_skills=[],
        resume_summary="",
        resume_years=None,
        resume_sections=[],
        evidence_chunks=[],
        missing_skills=[],
        gap_hints=[],
    )
    assert "Job: (unknown title) at (unknown company)" in prompt
    assert "Resume skills: (none listed)" in prompt
    assert "Resume summary: (none)" in prompt
    assert "Total experience: unknown" in prompt
    assert "Evidence chunks" not in prompt
    assert "resume lacks" not in prompt
    assert "skill-gap hints" not in prompt
    assert "Resume content by section" not in prompt


def test_build_cover_letter_prompt_format():
    """matched/missing 區塊只在有值時出現；證據編號同款。"""
    prompt = build_cover_letter_prompt(
        job_title="Data Engineer",
        job_company="Globex",
        evidence_chunks=[("responsibilities", "Own ETL pipelines")],
        matched_skills=["Python", "SQL"],
        missing_skills=["Spark"],
        **_RESUME_KWARGS,
    )
    assert "Job: Data Engineer at Globex" in prompt
    assert "[1] (responsibilities) Own ETL pipelines" in prompt
    assert "Candidate strengths matching the job: Python, SQL" in prompt
    assert "do not overclaim): Spark" in prompt

    no_match = build_cover_letter_prompt(
        job_title="Data Engineer",
        job_company="Globex",
        evidence_chunks=[],
        matched_skills=[],
        missing_skills=[],
        **_RESUME_KWARGS,
    )
    assert "Candidate strengths" not in no_match
    assert "do not overclaim" not in no_match


def test_build_interview_qs_prompt_format():
    """四類題型指令在 prompt 內；gap 區塊只在有 missing/hints 時出現。"""
    prompt = build_interview_qs_prompt(
        job_title="ML Engineer",
        job_company="Initech",
        evidence_chunks=[("required_skills", "PyTorch required")],
        missing_skills=["PyTorch"],
        gap_hints=[],
        **_RESUME_KWARGS,
    )
    assert "technical, behavioral, project-based, and" in prompt
    assert "skill-gap-focused" in prompt
    assert "Missing skills to probe in skill-gap-focused questions: PyTorch" in prompt
    assert "Known skill-gap hints" not in prompt  # 無 hints → 無區塊
    assert "--- experience ---" in prompt
