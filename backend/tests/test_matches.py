"""Match ranking + explanation 的 API 整合測試（FR-19~23）。

LLM 與 embedding 全程以假 provider 替換：``generate_structured`` 按 schema
分派（ResumeParsed / JobParsed / SkillEquivalenceResult / MatchExplanation），
``embed`` 回正交基底向量讓 cosine 恰為 0 / 1——組合分數可手算精確斷言。

幾何設定（每次 embed 呼叫向量索引從 0 起）：
- 履歷三段文字 → b0(summary) / b1(skills) / b2(experience)
- job chunks 依 section 順序 → b0(overview) / b1 / b2 / ...
故每種 kind 都會命中同索引 chunk（cos=1 → embedding_similarity=1.0），
而 title_similarity = rescale(cos(b2, b0)) = rescale(0) = 0.0。
"""

import uuid

import pytest
from sqlalchemy import select

from app.ai.llm.base import (
    EmbeddingResult,
    LLMError,
    LLMProvider,
    StructuredOutputError,
    StructuredResult,
    TokenUsage,
)
from app.ai.parsers.job_schema import JobParsed
from app.ai.parsers.match_schema import (
    MatchExplanation,
    SkillEquivalencePair,
    SkillEquivalenceResult,
)
from app.ai.parsers.resume_schema import BasicInfo, ExperienceItem, ResumeParsed
from app.api.jobs import get_llm_provider as jobs_get_llm_provider
from app.api.matches import get_llm_provider as matches_get_llm_provider
from app.api.resumes import get_llm_provider as resumes_get_llm_provider
from app.db.models import MatchResult, ResumeEmbedding
from app.main import app

_EMBEDDING_DIM = 768


def _basis(i: int) -> list[float]:
    """第 i 維為 1、其餘為 0 的單位向量（正交基底）。"""
    vec = [0.0] * _EMBEDDING_DIM
    vec[i] = 1.0
    return vec


# --- 固定資料 ------------------------------------------------------------------


def _sample_resume_parsed() -> ResumeParsed:
    """summary / skills / experience 三段皆非空 → 產生三種 kind 的向量。

    年資 Jan 2020 → Jan 2023 = 3.0 年。技能池 = ["Python", "Golang"]。
    """
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


def _strong_job_parsed() -> JobParsed:
    """手算分數 0.75 的 job：

    required ["Python", "Go"]：Python exact + Go≈Golang（LLM 等價）→ coverage 1.0
    preferred ["Docker"]：missing → 0.0
    embedding_similarity = 1.0（正交基底幾何）
    experience：3 年 vs "2+ years" → years 1.0；title_sim 0.0
      → alignment = 0.25×1.0 + 0.75×0.0 = 0.25（校準後加權，phase5_notes 問題 5）
    composite = 0.35×1 + 0.35×1 + 0.10×0 + 0.20×0.25 = 0.75
    """
    return JobParsed(
        company="Acme Corp",
        title="Backend Engineer",
        responsibilities=["Build APIs"],
        required_skills=["Python", "Go"],
        preferred_skills=["Docker"],
        experience_requirements=["2+ years backend"],
    )


def _weak_job_parsed() -> JobParsed:
    """手算分數 ≈0.422 的 job（無 preferred → 權重歸一化）：

    required ["Rust", "Kubernetes"] 全 missing（等價 pair 驗證不過）→ 0.0
    embedding_similarity = 1.0；3 年 vs "5+ years" → years 0.6；title 0.0
      → alignment = 0.25×0.6 = 0.15
    composite = (0.35×1 + 0.35×0 + 0.20×0.15) / 0.9 = 0.38 / 0.9
    """
    return JobParsed(
        company="Beta LLC",
        title="Data Scientist",
        responsibilities=["Analyze data"],
        required_skills=["Rust", "Kubernetes"],
        preferred_skills=[],
        experience_requirements=["5+ years"],
    )


_STRONG_SCORE = 0.75
_WEAK_SCORE = 0.38 / 0.9
# 無 embedding → title_similarity 也缺 → alignment 單邊用 years=1.0（校準前後同值）。
_NO_EMBEDDING_SCORE = (0.35 * 1.0 + 0.10 * 0.0 + 0.20 * 1.0) / 0.65


class _FakeProvider(LLMProvider):
    """按 schema 分派的假 provider——一顆同時服務解析、等價、explanation、embed。"""

    def __init__(
        self,
        *,
        resume_parsed: ResumeParsed | None = None,
        job_parsed: JobParsed | None = None,
        equivalences: list[tuple[str, str]] | None = None,
        resume_error: Exception | None = None,
        job_error: Exception | None = None,
        equivalence_error: Exception | None = None,
        explanation_error: Exception | None = None,
        embed_error: Exception | None = None,
    ):
        self._resume_parsed = (
            resume_parsed if resume_parsed is not None else _sample_resume_parsed()
        )
        self._job_parsed = job_parsed if job_parsed is not None else _strong_job_parsed()
        self._equivalences = equivalences if equivalences is not None else [("Go", "Golang")]
        self._resume_error = resume_error
        self._job_error = job_error
        self._equivalence_error = equivalence_error
        self._explanation_error = explanation_error
        self._embed_error = embed_error
        self.model = "fake-model"
        self.embedding_model = "fake-embed"

    def generate(self, prompt, *, system=None, temperature=0.7):  # pragma: no cover
        raise NotImplementedError

    def generate_structured(self, prompt, schema, *, system=None):
        data: object
        if schema is ResumeParsed:
            if self._resume_error is not None:
                raise self._resume_error
            data = self._resume_parsed
        elif schema is JobParsed:
            if self._job_error is not None:
                raise self._job_error
            data = self._job_parsed
        elif schema is SkillEquivalenceResult:
            if self._equivalence_error is not None:
                raise self._equivalence_error
            data = SkillEquivalenceResult(
                equivalences=[
                    SkillEquivalencePair(job_skill=j, resume_skill=r) for j, r in self._equivalences
                ]
            )
        elif schema is MatchExplanation:
            if self._explanation_error is not None:
                raise self._explanation_error
            data = MatchExplanation(
                why_matched="Strong overlap in backend skills.",
                top_overlap=["Python"],
                missing_skills=["Docker"],
                risks=["Limited preferred-skill coverage."],
            )
        else:  # pragma: no cover
            raise AssertionError(f"unexpected schema: {schema}")
        return StructuredResult(
            data=data,
            model=self.model,
            usage=TokenUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30),
        )

    def embed(self, texts, *, task_type="RETRIEVAL_DOCUMENT"):
        if self._embed_error is not None:
            raise self._embed_error
        return EmbeddingResult(
            vectors=[_basis(i) for i in range(len(texts))],
            model="fake-embed",
        )


@pytest.fixture
def use_provider(client):
    """把三個 router 的 get_llm_provider 全換掉（client teardown 自動清 override）。"""

    def _use(provider: LLMProvider) -> None:
        for key in (resumes_get_llm_provider, jobs_get_llm_provider, matches_get_llm_provider):
            app.dependency_overrides[key] = lambda: provider

    return _use


def _upload_resume(client, headers) -> str:
    resp = client.post(
        "/resumes/upload",
        data={"text_content": "Jane Smith resume text long enough."},
        headers=headers,
    )
    assert resp.status_code == 201
    return resp.json()["id"]


def _create_job(client, headers) -> str:
    resp = client.post(
        "/jobs", json={"raw_text": "A long enough job description text."}, headers=headers
    )
    assert resp.status_code == 201
    return resp.json()["id"]


# --- 測試 ---------------------------------------------------------------------


def test_run_happy_path_ranked_and_persisted(client, auth, use_provider, db_session):
    """兩個 job 批次匹配 → 200、按分數 desc、手算分數吻合、rows 持久化（FR-19/20/21/23）。"""
    headers = auth()["headers"]
    use_provider(_FakeProvider())
    resume_id = _upload_resume(client, headers)
    strong_id = _create_job(client, headers)
    use_provider(_FakeProvider(job_parsed=_weak_job_parsed()))
    weak_id = _create_job(client, headers)
    use_provider(_FakeProvider())

    resp = client.post(
        "/matches/run",
        json={"resume_id": resume_id, "job_ids": [weak_id, strong_id]},
        headers=headers,
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["resume_version_number"] == 1
    assert body["skipped"] == []
    assert [r["job_id"] for r in body["results"]] == [strong_id, weak_id]
    assert body["results"][0]["match_score"] == pytest.approx(_STRONG_SCORE)
    assert body["results"][1]["match_score"] == pytest.approx(_WEAK_SCORE)
    assert body["results"][0]["job_title"] == "Backend Engineer"
    assert body["results"][0]["explanation"]["why_matched"]

    rows = db_session.scalars(select(MatchResult)).all()
    assert len(rows) == 2


def test_equivalence_counts_as_matched(client, auth, use_provider):
    """ "Go" 經 LLM 等價 "Golang" 計入 matched；coverage 與 pairs 精確（FR-20）。"""
    headers = auth()["headers"]
    use_provider(_FakeProvider())
    resume_id = _upload_resume(client, headers)
    job_id = _create_job(client, headers)

    resp = client.post(
        "/matches/run", json={"resume_id": resume_id, "job_ids": [job_id]}, headers=headers
    )

    breakdown = resp.json()["results"][0]["breakdown"]
    assert sorted(breakdown["matched_required"]) == ["Go", "Python"]
    assert breakdown["missing_required"] == []
    assert breakdown["required_coverage"] == pytest.approx(1.0)
    assert breakdown["missing_preferred"] == ["Docker"]
    assert breakdown["llm_equivalence_used"] is True
    assert breakdown["equivalent_pairs"] == [{"job_skill": "Go", "resume_skill": "Golang"}]


def test_rerun_upserts_single_row(client, auth, use_provider, db_session):
    """重跑覆蓋：每對 (resume, job) 只留一筆、同 row id、updated_at 前進（FR-23）。"""
    headers = auth()["headers"]
    use_provider(_FakeProvider())
    resume_id = _upload_resume(client, headers)
    job_id = _create_job(client, headers)

    first = client.post(
        "/matches/run", json={"resume_id": resume_id, "job_ids": [job_id]}, headers=headers
    ).json()["results"][0]
    second = client.post(
        "/matches/run", json={"resume_id": resume_id, "job_ids": [job_id]}, headers=headers
    ).json()["results"][0]

    rows = db_session.scalars(select(MatchResult)).all()
    assert len(rows) == 1
    assert first["id"] == second["id"]
    assert second["updated_at"] > first["updated_at"]  # ISO 字串同格式，字典序即時間序


def test_explanation_failure_degrades(client, auth, use_provider, db_session):
    """Explanation 生成失敗 → 200、分數照存、explanation=null + error 訊息（FR-22 / NFR-4）。"""
    headers = auth()["headers"]
    use_provider(_FakeProvider())
    resume_id = _upload_resume(client, headers)
    job_id = _create_job(client, headers)
    use_provider(_FakeProvider(explanation_error=StructuredOutputError("explanation down")))

    resp = client.post(
        "/matches/run", json={"resume_id": resume_id, "job_ids": [job_id]}, headers=headers
    )

    assert resp.status_code == 200
    item = resp.json()["results"][0]
    assert item["match_score"] == pytest.approx(_STRONG_SCORE)
    assert item["explanation"] is None
    assert "explanation down" in item["explanation_error"]

    row = db_session.scalars(select(MatchResult)).one()
    assert row.explanation is None and row.explanation_error


def test_per_job_skipped_reasons(client, auth, use_provider):
    """不存在的 job → not_found；解析失敗的 job → not_parsed；好 job 照常計分（NFR-4）。"""
    headers = auth()["headers"]
    use_provider(_FakeProvider())
    resume_id = _upload_resume(client, headers)
    good_id = _create_job(client, headers)
    use_provider(_FakeProvider(job_error=StructuredOutputError("bad job text")))
    failed_id = _create_job(client, headers)
    use_provider(_FakeProvider())
    ghost_id = str(uuid.uuid4())

    resp = client.post(
        "/matches/run",
        json={"resume_id": resume_id, "job_ids": [good_id, ghost_id, failed_id]},
        headers=headers,
    )

    body = resp.json()
    assert [r["job_id"] for r in body["results"]] == [good_id]
    assert {(s["job_id"], s["reason"]) for s in body["skipped"]} == {
        (ghost_id, "not_found"),
        (failed_id, "not_parsed"),
    }


def test_embedding_degradation_then_lazy_backfill(client, auth, use_provider, db_session):
    """Embedding 一直失敗 → 成分 None、權重歸一化照樣計分；恢復後重跑 → backfill 補齊。"""
    headers = auth()["headers"]
    use_provider(_FakeProvider(embed_error=LLMError("embed down")))
    resume_id = _upload_resume(client, headers)  # 儲存成功、無向量
    job_id = _create_job(client, headers)  # index_status=failed、無 job 向量

    resp = client.post(
        "/matches/run", json={"resume_id": resume_id, "job_ids": [job_id]}, headers=headers
    )
    item = resp.json()["results"][0]
    assert item["breakdown"]["embedding_similarity"] is None
    assert item["breakdown"]["title_similarity"] is None
    assert item["match_score"] == pytest.approx(_NO_EMBEDDING_SCORE)
    assert db_session.scalars(select(ResumeEmbedding)).all() == []

    # embed 恢復 → lazy backfill 產生履歷向量；job 向量仍缺（job 建立時已定案），
    # 每種 kind 對「零個 chunk」仍無相似度 → 成分维持 None，但 rows 已補齊。
    use_provider(_FakeProvider())
    client.post("/matches/run", json={"resume_id": resume_id, "job_ids": [job_id]}, headers=headers)
    kinds = {r.kind for r in db_session.scalars(select(ResumeEmbedding)).all()}
    assert kinds == {"summary", "skills", "experience"}


def test_get_matches_sorted_and_isolated(client, auth, use_provider):
    """GET /matches 按分數 desc；他人 resume_id → 404；未登入 → 401（FR-21 / FR-4）。"""
    a_headers = auth(email="alice@example.com")["headers"]
    use_provider(_FakeProvider())
    resume_id = _upload_resume(client, a_headers)
    strong_id = _create_job(client, a_headers)
    use_provider(_FakeProvider(job_parsed=_weak_job_parsed()))
    weak_id = _create_job(client, a_headers)
    use_provider(_FakeProvider())
    client.post(
        "/matches/run",
        json={"resume_id": resume_id, "job_ids": [strong_id, weak_id]},
        headers=a_headers,
    )

    listed = client.get(f"/matches?resume_id={resume_id}", headers=a_headers)
    assert listed.status_code == 200
    scores = [r["match_score"] for r in listed.json()]
    assert scores == sorted(scores, reverse=True)
    assert [r["job_id"] for r in listed.json()] == [strong_id, weak_id]

    b_headers = auth(email="bob@example.com")["headers"]
    assert client.get(f"/matches?resume_id={resume_id}", headers=b_headers).status_code == 404
    assert client.get(f"/matches?resume_id={resume_id}").status_code == 401
    assert (
        client.post(
            "/matches/run", json={"resume_id": resume_id, "job_ids": [strong_id]}
        ).status_code
        == 401
    )


def test_run_resume_not_ready_or_missing(client, auth, use_provider):
    """履歷解析失敗 → 409；不存在的 resume_id → 404（FR-19 前置驗證）。"""
    headers = auth()["headers"]
    use_provider(_FakeProvider(resume_error=StructuredOutputError("bad resume")))
    resume_id = _upload_resume(client, headers)  # parse_status=failed、無版本
    use_provider(_FakeProvider())
    job_id = _create_job(client, headers)

    resp = client.post(
        "/matches/run", json={"resume_id": resume_id, "job_ids": [job_id]}, headers=headers
    )
    assert resp.status_code == 409

    ghost = client.post(
        "/matches/run",
        json={"resume_id": str(uuid.uuid4()), "job_ids": [job_id]},
        headers=headers,
    )
    assert ghost.status_code == 404
