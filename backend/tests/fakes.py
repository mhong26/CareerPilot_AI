"""共用測試假件（Phase 9 新測試用）。

整併 test_matches / test_application_kit 各自維護的 fake 素材；既有測試模組
本 phase 不回頭改動（避免攪動已綠的測試），新測試（e2e、error handlers、
input limits）統一從這裡取。
"""

from app.ai.llm.base import (
    EmbeddingResult,
    LLMProvider,
    StructuredResult,
    TokenUsage,
)
from app.ai.parsers.job_schema import JobParsed
from app.ai.parsers.kit_schema import (
    CoverLetterDraft,
    InterviewPrepSet,
    InterviewQuestion,
    TailoredResumeSuggestions,
)
from app.ai.parsers.match_schema import (
    MatchExplanation,
    SkillEquivalencePair,
    SkillEquivalenceResult,
)
from app.ai.parsers.resume_schema import BasicInfo, ExperienceItem, ResumeParsed

EMBEDDING_DIM = 768


def basis(i: int) -> list[float]:
    """第 i 維為 1、其餘為 0 的單位向量（正交基底）——cosine 恰為 0 / 1。"""
    vec = [0.0] * EMBEDDING_DIM
    vec[i] = 1.0
    return vec


def sample_resume_parsed() -> ResumeParsed:
    return ResumeParsed(
        basic_info=BasicInfo(name="Jane Smith", email="jane@example.com"),
        summary="Backend engineer.",
        skills=["Python", "Golang"],
        experience=[
            ExperienceItem(
                company="Acme",
                title="Engineer",
                start_date="Jan 2020",
                end_date="Jan 2023",
                bullets=["Built APIs"],
            )
        ],
    )


def sample_job_parsed() -> JobParsed:
    return JobParsed(
        company="Acme Corp",
        title="Backend Engineer",
        responsibilities=["Build APIs"],
        required_skills=["Python", "Go"],
        preferred_skills=["Docker"],
        experience_requirements=["2+ years backend"],
    )


class FakeProvider(LLMProvider):
    """按 schema 分派的全能假 provider：解析、等價、explanation、三類 kit、embed。"""

    def __init__(self):
        self.model = "fake-model"
        self.embedding_model = "fake-embed"

    def generate(self, prompt, *, system=None, temperature=0.7):  # pragma: no cover
        raise NotImplementedError

    def generate_structured(self, prompt, schema, *, system=None):
        data: object
        if schema is ResumeParsed:
            data = sample_resume_parsed()
        elif schema is JobParsed:
            data = sample_job_parsed()
        elif schema is SkillEquivalenceResult:
            data = SkillEquivalenceResult(
                equivalences=[SkillEquivalencePair(job_skill="Go", resume_skill="Golang")]
            )
        elif schema is MatchExplanation:
            data = MatchExplanation(
                why_matched="Strong overlap in backend skills.",
                top_overlap=["Python"],
                missing_skills=["Docker"],
                risks=["Limited preferred-skill coverage."],
            )
        elif schema is TailoredResumeSuggestions:
            data = TailoredResumeSuggestions(overall_strategy="Lead with backend work.")
        elif schema is CoverLetterDraft:
            data = CoverLetterDraft(
                intro="Dear team,", body_paragraphs=["I built APIs."], closing="Thanks."
            )
        elif schema is InterviewPrepSet:
            data = InterviewPrepSet(
                questions=[InterviewQuestion(question="Why us?", category="behavioral")]
            )
        else:  # pragma: no cover
            raise AssertionError(f"unexpected schema: {schema}")
        return StructuredResult(
            data=data,
            model=self.model,
            usage=TokenUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30),
        )

    def embed(self, texts, *, task_type="RETRIEVAL_DOCUMENT"):
        return EmbeddingResult(
            vectors=[basis(i) for i in range(len(texts))],
            model="fake-embed",
        )


class NoopReranker:
    """遞減分數 → 保持向量序；絕不載入真 cross-encoder。"""

    def predict(self, query: str, passages: list[str]) -> list[float]:
        return [float(len(passages) - i) for i in range(len(passages))]


# 極小但真實可解析的單頁 PDF（pypdf 對缺 xref 會自行重建，僅發 warning）。
MINIMAL_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
    b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
    b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
    b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >> endobj\n"
    b"4 0 obj << /Length 68 >> stream\n"
    b"BT /F1 12 Tf 72 720 Td (Hello resume world from a tiny PDF file) Tj ET\n"
    b"endstream endobj\n"
    b"5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n"
    b"trailer << /Root 1 0 R >>\n"
    b"startxref\n0\n%%EOF\n"
)


def make_docx_bytes(text: str) -> bytes:
    """用 python-docx 在記憶體組出真 DOCX。"""
    import io

    from docx import Document

    buf = io.BytesIO()
    doc = Document()
    doc.add_paragraph(text)
    doc.save(buf)
    return buf.getvalue()
