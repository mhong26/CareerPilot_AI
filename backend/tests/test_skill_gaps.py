"""Skill gap 的 API 整合測試（FR-24~30）。

LLM 與 embedding 以假 provider 替換（schema 分派同 test_matches），reranker
以假物件經 dependency_overrides 注入——CI 永不下載 cross-encoder 模型。

幾何設定（正交基底，cosine 恰為 0 / 1；每次 embed 呼叫向量索引從 0 起）：
- 履歷三段文字 → b0(summary) / b1(skills) / b2(experience)；query kind=skills → b1
- gap 測試用 2-chunk job（overview 欄位全空 → 略過）：
  chunk0=responsibilities(b0)、chunk1=required_skills(b1)
  → 向量序恆為 [required_skills, responsibilities]（sim 1.0 / 0.0），無平手模糊。
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
from app.ai.parsers.resume_schema import BasicInfo, ExperienceItem, ResumeParsed
from app.ai.parsers.skill_gap_schema import SkillGapAnalysis, SkillGapItem
from app.api.jobs import get_llm_provider as jobs_get_llm_provider
from app.api.resumes import get_llm_provider as resumes_get_llm_provider
from app.api.skill_gaps import get_llm_provider as skill_gaps_get_llm_provider
from app.api.skill_gaps import get_reranker as skill_gaps_get_reranker
from app.db.models import JobChunk, SkillGapReport
from app.main import app

_EMBEDDING_DIM = 768


def _basis(i: int) -> list[float]:
    """第 i 維為 1、其餘為 0 的單位向量（正交基底）。"""
    vec = [0.0] * _EMBEDDING_DIM
    vec[i] = 1.0
    return vec


# --- 固定資料 ------------------------------------------------------------------


def _sample_resume_parsed() -> ResumeParsed:
    """summary / skills / experience 三段皆非空 → 產生三種 kind 的向量。"""
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


def _gap_job_parsed() -> JobParsed:
    """2-chunk job：overview 欄位全空 → 只有 responsibilities 與 required_skills。

    chunk0=responsibilities(b0)、chunk1=required_skills(b1)；query=skills(b1)
    → 向量序 [required_skills(sim 1.0), responsibilities(sim 0.0)]，完全確定。
    """
    return JobParsed(responsibilities=["Build APIs"], required_skills=["Python", "Go"])


def _six_chunk_job_parsed() -> JobParsed:
    """6-chunk job（含 overview 與 qualifications）→ 驗證 top-k=5 有實際截斷。

    chunk 順序：overview(b0) / responsibilities(b1) / required_skills(b2) /
    preferred_skills(b3) / qualifications(b4) / experience_requirements(b5)。
    query=skills(b1) → 第一名恆為 responsibilities；其餘同分（sim 0），只斷言
    tie-safe 事實（首位 + 數量 + 歸屬）。
    """
    return JobParsed(
        company="Acme Corp",
        title="Backend Engineer",
        responsibilities=["Build APIs"],
        required_skills=["Python", "Go"],
        preferred_skills=["Docker"],
        qualifications=["BSc in CS"],
        experience_requirements=["2+ years backend"],
    )


def _sample_skill_gap() -> SkillGapAnalysis:
    """預設一條 gap，引用第 1 個（rerank 後）證據 chunk。"""
    return SkillGapAnalysis(
        gaps=[
            SkillGapItem(
                skill="Go",
                severity="high",
                reason="The job requires Go but the resume shows only Golang basics.",
                evidence_chunk_numbers=[1],
                suggestion="Ship a small production-style service in Go.",
            )
        ],
        overall_summary="Solid backend base with one language gap.",
    )


class _FakeProvider(LLMProvider):
    """按 schema 分派的假 provider——同時服務解析、skill gap 生成與 embed。"""

    def __init__(
        self,
        *,
        resume_parsed: ResumeParsed | None = None,
        job_parsed: JobParsed | None = None,
        skill_gap: SkillGapAnalysis | None = None,
        resume_error: Exception | None = None,
        job_error: Exception | None = None,
        skill_gap_error: Exception | None = None,
        embed_error: Exception | None = None,
    ):
        self._resume_parsed = (
            resume_parsed if resume_parsed is not None else _sample_resume_parsed()
        )
        self._job_parsed = job_parsed if job_parsed is not None else _gap_job_parsed()
        self._skill_gap = skill_gap if skill_gap is not None else _sample_skill_gap()
        self._resume_error = resume_error
        self._job_error = job_error
        self._skill_gap_error = skill_gap_error
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
        elif schema is SkillGapAnalysis:
            if self._skill_gap_error is not None:
                raise self._skill_gap_error
            data = self._skill_gap
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


# --- 假 rerankers ---------------------------------------------------------------


class _NoopReranker:
    """遞減分數 → 保持向量序；預設注入，確保測試絕不載入真模型。"""

    def predict(self, query: str, passages: list[str]) -> list[float]:
        return [float(len(passages) - i) for i in range(len(passages))]


class _KeywordReranker:
    """ "Responsibilities" 給高分 → 與向量序可觀察地不同（rerank 效果可斷言）。"""

    def predict(self, query: str, passages: list[str]) -> list[float]:
        return [10.0 if "Responsibilities" in p else float(-i) for i, p in enumerate(passages)]


class _FailingReranker:
    def predict(self, query: str, passages: list[str]) -> list[float]:
        raise RuntimeError("rerank down")


# --- fixtures / helpers ---------------------------------------------------------


@pytest.fixture
def use_provider(client):
    """把三個 router 的 get_llm_provider 全換掉（client teardown 自動清 override）。"""

    def _use(provider: LLMProvider) -> None:
        for key in (
            resumes_get_llm_provider,
            jobs_get_llm_provider,
            skill_gaps_get_llm_provider,
        ):
            app.dependency_overrides[key] = lambda: provider

    return _use


@pytest.fixture(autouse=True)
def use_reranker(client):
    """預設注入 _NoopReranker（autouse：任何測試都不會碰真 cross-encoder）；
    回 setter 供個別測試替換。"""

    def _use(reranker) -> None:
        app.dependency_overrides[skill_gaps_get_reranker] = lambda: reranker

    _use(_NoopReranker())
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


def _run(client, headers, job_id: str, resume_id: str):
    return client.post(f"/jobs/{job_id}/skill-gap", json={"resume_id": resume_id}, headers=headers)


# --- 測試 ---------------------------------------------------------------------


def test_run_happy_path_persists_report(client, auth, use_provider, db_session):
    """檢索排序正確、gap 引用映射為 chunk UUID、chunks 附原文、單 row 落地
    （FR-24/25/27/28/30）。"""
    headers = auth()["headers"]
    use_provider(_FakeProvider())
    resume_id = _upload_resume(client, headers)
    job_id = _create_job(client, headers)

    resp = _run(client, headers, job_id, resume_id)

    assert resp.status_code == 200
    body = resp.json()
    assert body["resume_version_number"] == 1
    retrieval = body["retrieval"]
    assert retrieval["query_kind"] == "skills"
    assert retrieval["top_k"] == 5
    # 向量序：required_skills 命中 skills 向量（sim 1.0），responsibilities 0.0。
    assert [c["section"] for c in retrieval["chunks"]] == ["required_skills", "responsibilities"]
    assert retrieval["chunks"][0]["cosine_similarity"] == pytest.approx(1.0)
    assert retrieval["chunks"][1]["cosine_similarity"] == pytest.approx(0.0)
    assert retrieval["rerank_used"] is True
    # Noop reranker 保持向量序 → ranked 首位 = required_skills；gap 引用 [1] 映射到它。
    assert body["chunks"][0]["section"] == "required_skills"
    assert "Python" in body["chunks"][0]["content"]
    gaps = body["analysis"]["gaps"]
    assert len(gaps) == 1
    assert gaps[0]["skill"] == "Go"
    assert gaps[0]["severity"] == "high"
    assert gaps[0]["evidence_chunk_ids"] == [body["chunks"][0]["id"]]
    assert body["analysis"]["dropped_gap_count"] == 0
    assert body["generation_error"] is None

    rows = db_session.scalars(select(SkillGapReport)).all()
    assert len(rows) == 1
    assert rows[0].analysis is not None and len(rows[0].analysis["gaps"]) == 1


def test_retrieval_is_job_scoped_and_topk(client, auth, use_provider, db_session):
    """top-k=5 對 6-chunk job 有實際截斷；結果只含目標 job 的 chunks（FR-25）。

    兩個 job 的 chunk 向量完全相同（各自 embed 都是 b0..b5），沒有 job 過濾
    就會混到 job B——歸屬斷言即在測這個 WHERE 條件。
    """
    headers = auth()["headers"]
    use_provider(_FakeProvider(job_parsed=_six_chunk_job_parsed()))
    resume_id = _upload_resume(client, headers)
    job_a = _create_job(client, headers)
    _job_b = _create_job(client, headers)

    resp = _run(client, headers, job_a, resume_id)

    assert resp.status_code == 200
    retrieval = resp.json()["retrieval"]
    assert len(retrieval["chunks"]) == 5  # 6 chunks 截到 top-5
    # skills(b1) 命中 chunk_index=1 = responsibilities；其餘同分不斷言順序。
    assert retrieval["chunks"][0]["section"] == "responsibilities"
    job_a_chunk_ids = {
        str(cid)
        for cid in db_session.scalars(
            select(JobChunk.id).where(JobChunk.job_id == uuid.UUID(job_a))
        ).all()
    }
    assert {c["chunk_id"] for c in retrieval["chunks"]} <= job_a_chunk_ids


def test_rerank_reorders_and_records_both_orders(client, auth, use_provider, use_reranker):
    """rerank 改變排序；向量序（含兩種分數）與 rerank 後序都保存（FR-26、ER-5）。"""
    headers = auth()["headers"]
    use_provider(_FakeProvider())
    use_reranker(_KeywordReranker())
    resume_id = _upload_resume(client, headers)
    job_id = _create_job(client, headers)

    resp = _run(client, headers, job_id, resume_id)

    assert resp.status_code == 200
    body = resp.json()
    retrieval = body["retrieval"]
    assert retrieval["rerank_used"] is True
    assert retrieval["rerank_model"] is not None
    assert retrieval["rerank_error"] is None
    # 向量序不變（pre-rerank 快照），且每塊都帶 rerank 分數。
    assert [c["section"] for c in retrieval["chunks"]] == ["required_skills", "responsibilities"]
    assert all(c["rerank_score"] is not None for c in retrieval["chunks"])
    # rerank 後序被關鍵字翻轉：responsibilities 升到第一。
    assert retrieval["ranked_chunk_ids"][0] != retrieval["chunks"][0]["chunk_id"]
    assert body["chunks"][0]["section"] == "responsibilities"
    # gap 引用 [1] 依 rerank 後序映射 → responsibilities chunk。
    assert body["analysis"]["gaps"][0]["evidence_chunk_ids"] == [body["chunks"][0]["id"]]


def test_rerank_failure_falls_back_to_vector_order(client, auth, use_provider, use_reranker):
    """reranker 掛掉 → 降級向量序、記 rerank_error，分析照樣生成（NFR-4）。"""
    headers = auth()["headers"]
    use_provider(_FakeProvider())
    use_reranker(_FailingReranker())
    resume_id = _upload_resume(client, headers)
    job_id = _create_job(client, headers)

    resp = _run(client, headers, job_id, resume_id)

    assert resp.status_code == 200
    body = resp.json()
    retrieval = body["retrieval"]
    assert retrieval["rerank_used"] is False
    assert "rerank down" in retrieval["rerank_error"]
    assert retrieval["rerank_model"] is None
    assert retrieval["ranked_chunk_ids"] == [c["chunk_id"] for c in retrieval["chunks"]]
    assert body["analysis"] is not None


def test_citation_validation_drops_hallucinated_ids(client, auth, use_provider):
    """幻覺編號防護：越界編號剔除、零證據 gap 整條丟棄並計數、severity 正規化
    （FR-28/29）。"""
    headers = auth()["headers"]
    use_provider(
        _FakeProvider(
            skill_gap=SkillGapAnalysis(
                gaps=[
                    SkillGapItem(skill="Go", severity="HIGH", evidence_chunk_numbers=[1]),
                    SkillGapItem(skill="Ghost", severity="high", evidence_chunk_numbers=[99]),
                    SkillGapItem(skill="Rust", severity="low", evidence_chunk_numbers=[1, 2, 99]),
                ],
                overall_summary="ok",
            )
        )
    )
    resume_id = _upload_resume(client, headers)
    job_id = _create_job(client, headers)

    resp = _run(client, headers, job_id, resume_id)

    assert resp.status_code == 200
    body = resp.json()
    ranked_ids = [c["id"] for c in body["chunks"]]
    gaps = body["analysis"]["gaps"]
    assert [g["skill"] for g in gaps] == ["Go", "Rust"]  # Ghost（全越界）被丟棄
    assert body["analysis"]["dropped_gap_count"] == 1
    assert gaps[0]["severity"] == "high"  # "HIGH" 正規化
    assert gaps[0]["evidence_chunk_ids"] == [ranked_ids[0]]
    assert gaps[1]["evidence_chunk_ids"] == ranked_ids[:2]  # 99 剔除、1/2 保留


def test_rerun_upserts_single_row(client, auth, use_provider, db_session):
    """重跑覆蓋：每對 (resume, job) 只留一筆、同 row id、updated_at 前進。"""
    headers = auth()["headers"]
    use_provider(_FakeProvider())
    resume_id = _upload_resume(client, headers)
    job_id = _create_job(client, headers)

    first = _run(client, headers, job_id, resume_id).json()
    second = _run(client, headers, job_id, resume_id).json()

    rows = db_session.scalars(select(SkillGapReport)).all()
    assert len(rows) == 1
    assert first["id"] == second["id"]
    assert second["updated_at"] > first["updated_at"]  # ISO 字串同格式，字典序即時間序


def test_generation_failure_degrades(client, auth, use_provider, db_session):
    """LLM 生成失敗 → 200、analysis 為 null、error 留言，檢索結果照存（NFR-4）。"""
    headers = auth()["headers"]
    use_provider(_FakeProvider(skill_gap_error=StructuredOutputError("gap gen down")))
    resume_id = _upload_resume(client, headers)
    job_id = _create_job(client, headers)

    resp = _run(client, headers, job_id, resume_id)

    assert resp.status_code == 200
    body = resp.json()
    assert body["analysis"] is None
    assert "gap gen down" in body["generation_error"]
    assert len(body["retrieval"]["chunks"]) == 2  # 檢索不因生成失敗連坐

    row = db_session.scalars(select(SkillGapReport)).one()
    assert row.analysis is None
    assert "gap gen down" in row.generation_error


def test_not_ready_conflicts(client, auth, use_provider):
    """履歷未解析 / 職缺未索引 → 409；幽靈 id → 404。"""
    headers = auth()["headers"]

    # 履歷解析失敗 → 無可用版本 → 409。
    use_provider(_FakeProvider(resume_error=StructuredOutputError("bad resume")))
    failed_resume_id = _upload_resume(client, headers)
    use_provider(_FakeProvider())
    job_id = _create_job(client, headers)
    resp = _run(client, headers, job_id, failed_resume_id)
    assert resp.status_code == 409
    assert "no parsed version" in resp.json()["detail"]

    # 職缺 embedding 失敗 → index_status=failed → 409（RAG 無證據可引用）。
    resume_id = _upload_resume(client, headers)
    use_provider(_FakeProvider(embed_error=LLMError("embed down")))
    unindexed_job_id = _create_job(client, headers)
    use_provider(_FakeProvider())
    resp = _run(client, headers, unindexed_job_id, resume_id)
    assert resp.status_code == 409
    assert "not indexed" in resp.json()["detail"]

    # 幽靈 id：resume / job 各自 404。
    assert _run(client, headers, job_id, str(uuid.uuid4())).status_code == 404
    assert _run(client, headers, str(uuid.uuid4()), resume_id).status_code == 404


def test_get_endpoints_and_isolation(client, auth, use_provider):
    """pair GET（分析前 404 = 尚未分析）、報告 GET；他人一律 404、未登入 401（FR-4）。"""
    headers = auth()["headers"]
    use_provider(_FakeProvider())
    resume_id = _upload_resume(client, headers)
    job_id = _create_job(client, headers)

    # 尚未分析：pair 查詢 404（前端據此顯示 Analyze 按鈕）。
    resp = client.get(f"/jobs/{job_id}/skill-gap", params={"resume_id": resume_id}, headers=headers)
    assert resp.status_code == 404

    report_id = _run(client, headers, job_id, resume_id).json()["id"]

    resp = client.get(f"/jobs/{job_id}/skill-gap", params={"resume_id": resume_id}, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["id"] == report_id
    resp = client.get(f"/skill-gaps/{report_id}", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["id"] == report_id

    # 他人視角：Alice 的資源一律 404（不可探測）。
    bob_headers = auth(email="bob@example.com")["headers"]
    assert (
        client.get(
            f"/jobs/{job_id}/skill-gap", params={"resume_id": resume_id}, headers=bob_headers
        ).status_code
        == 404
    )
    assert client.get(f"/skill-gaps/{report_id}", headers=bob_headers).status_code == 404
    assert _run(client, bob_headers, job_id, resume_id).status_code == 404

    # 未登入：401。
    assert (
        client.post(f"/jobs/{job_id}/skill-gap", json={"resume_id": resume_id}).status_code == 401
    )
    assert (
        client.get(f"/jobs/{job_id}/skill-gap", params={"resume_id": resume_id}).status_code == 401
    )
    assert client.get(f"/skill-gaps/{report_id}").status_code == 401
