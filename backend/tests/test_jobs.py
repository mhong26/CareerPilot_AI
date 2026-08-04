"""Job ingestion / 解析 / 切塊 / 向量索引的 API + pipeline 測試（FR-13~18）。

LLM 全程以假 provider 替換（override ``get_llm_provider``），不連網、不花 token。
假 provider 的 ``embed`` 回傳 768 維正交基底向量（第 i 塊在第 i 維為 1），使 cosine
距離成為可精確斷言的確定值（自己 = 0、彼此 = 1），得以驗證真實的 pgvector 查詢。
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
from app.ai.rag.chunking import chunk_job
from app.api.jobs import get_llm_provider
from app.db.models import JobChunk, JobEmbedding, User
from app.main import app
from app.services.job_service import create_job_from_parsed

_EMBEDDING_DIM = 768


def _sample_job_parsed() -> JobParsed:
    return JobParsed(
        company="Acme Corp",
        title="Backend Engineer",
        location="Remote",
        work_mode="remote",
        responsibilities=["Build APIs", "Own services"],
        required_skills=["Python", "FastAPI", "PostgreSQL"],
        preferred_skills=["Docker"],
        qualifications=["BS in CS"],
        experience_requirements=["3+ years backend"],
    )


def _basis(i: int) -> list[float]:
    """第 i 維為 1、其餘為 0 的單位向量（正交基底）。"""
    vec = [0.0] * _EMBEDDING_DIM
    vec[i] = 1.0
    return vec


class _FakeProvider(LLMProvider):
    """可設定解析結果 / 解析錯誤 / embedding 錯誤的假 provider。"""

    def __init__(
        self,
        *,
        parsed: JobParsed | None = None,
        parse_error: Exception | None = None,
        embed_error: Exception | None = None,
    ):
        self._parsed = parsed if parsed is not None else _sample_job_parsed()
        self._parse_error = parse_error
        self._embed_error = embed_error
        self.model = "fake-model"
        self.embedding_model = "fake-embed"

    def generate(self, prompt, *, system=None, temperature=0.7):  # pragma: no cover
        raise NotImplementedError

    def generate_structured(self, prompt, schema, *, system=None):
        if self._parse_error is not None:
            raise self._parse_error
        return StructuredResult(
            data=self._parsed,
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
    """把 get_llm_provider 換成指定的假 provider（client teardown 會自動清掉 override）。"""

    def _use(provider: LLMProvider) -> None:
        app.dependency_overrides[get_llm_provider] = lambda: provider

    return _use


# --- 測試 ---------------------------------------------------------------------


def test_create_job_happy_path(client, auth, use_provider, db_session):
    """貼文字 → 解析成功 → 切塊 + 向量寫入，狀態 parsed/indexed。"""
    use_provider(_FakeProvider())
    headers = auth()["headers"]

    resp = client.post(
        "/jobs", json={"raw_text": "A long enough job description text."}, headers=headers
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["parse_status"] == "parsed"
    assert body["index_status"] == "indexed"
    assert body["company"] == "Acme Corp"
    assert body["title"] == "Backend Engineer"
    assert body["parsed_data"]["required_skills"] == ["Python", "FastAPI", "PostgreSQL"]

    expected_chunks = len(chunk_job(_sample_job_parsed()))
    assert body["chunk_count"] == expected_chunks

    job_id = uuid.UUID(body["id"])
    chunks = db_session.scalars(
        select(JobChunk).where(JobChunk.job_id == job_id).order_by(JobChunk.chunk_index)
    ).all()
    assert len(chunks) == expected_chunks
    assert chunks[0].section == "overview"
    embeddings = db_session.scalars(select(JobEmbedding).where(JobEmbedding.job_id == job_id)).all()
    assert len(embeddings) == expected_chunks
    assert all(e.model == "fake-embed" for e in embeddings)


def test_create_job_parse_failure_still_saves(client, auth, use_provider, db_session):
    """解析失敗 → 仍回 201、parse_status=failed、index_status=skipped、無 chunk（FR-10）。"""
    use_provider(_FakeProvider(parse_error=StructuredOutputError("bad schema")))
    headers = auth()["headers"]

    resp = client.post(
        "/jobs", json={"raw_text": "Some job text that is long enough."}, headers=headers
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["parse_status"] == "failed"
    assert body["parse_error"]
    assert body["index_status"] == "skipped"
    assert body["parsed_data"] is None
    assert body["chunk_count"] == 0

    job_id = uuid.UUID(body["id"])
    assert db_session.scalars(select(JobChunk).where(JobChunk.job_id == job_id)).all() == []


def test_create_job_embed_failure_saves_chunks_marks_failed(client, auth, use_provider, db_session):
    """embedding 失敗 → chunks 照存、index_status=failed、無 embeddings（使用者決策）。"""
    use_provider(_FakeProvider(embed_error=LLMError("quota exceeded")))
    headers = auth()["headers"]

    resp = client.post(
        "/jobs", json={"raw_text": "Some job text that is long enough."}, headers=headers
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["parse_status"] == "parsed"
    assert body["index_status"] == "failed"
    assert body["index_error"]
    assert body["chunk_count"] > 0

    job_id = uuid.UUID(body["id"])
    assert len(db_session.scalars(select(JobChunk).where(JobChunk.job_id == job_id)).all()) > 0
    assert db_session.scalars(select(JobEmbedding).where(JobEmbedding.job_id == job_id)).all() == []


def test_vector_write_and_cosine_ordering(client, auth, use_provider, db_session):
    """真實 pgvector cosine 查詢：查詢 basis(1) → chunk_index=1 距離≈0 排第一、其餘≈1。"""
    use_provider(_FakeProvider())
    headers = auth()["headers"]

    body = client.post(
        "/jobs", json={"raw_text": "A long enough job description."}, headers=headers
    ).json()
    job_id = uuid.UUID(body["id"])

    rows = db_session.execute(
        select(
            JobChunk.chunk_index,
            JobEmbedding.vector.cosine_distance(_basis(1)).label("dist"),
        )
        .select_from(JobEmbedding)
        .join(JobChunk, JobChunk.id == JobEmbedding.chunk_id)
        .where(JobEmbedding.job_id == job_id)
        .order_by("dist")
    ).all()

    assert rows[0][0] == 1
    assert rows[0][1] == pytest.approx(0.0, abs=1e-6)
    assert all(row[1] == pytest.approx(1.0, abs=1e-6) for row in rows[1:])


def test_list_jobs_newest_first_light_fields(client, auth, use_provider):
    """列表：兩筆、最新在前、只含輕量欄位（無 parsed_data / raw_text）。"""
    use_provider(_FakeProvider())
    headers = auth()["headers"]

    client.post("/jobs", json={"raw_text": "First job description text."}, headers=headers)
    second = client.post(
        "/jobs", json={"raw_text": "Second job description text."}, headers=headers
    ).json()

    resp = client.get("/jobs", headers=headers)
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 2
    assert items[0]["id"] == second["id"]
    assert items[0]["index_status"] == "indexed"
    assert "parsed_data" not in items[0]
    assert "raw_text" not in items[0]


def test_get_job_detail(client, auth, use_provider):
    """詳細頁：回完整 parsed_data / raw_text / chunk_count。"""
    use_provider(_FakeProvider())
    headers = auth()["headers"]

    jid = client.post(
        "/jobs", json={"raw_text": "A job description text."}, headers=headers
    ).json()["id"]

    resp = client.get(f"/jobs/{jid}", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["raw_text"]
    assert body["parsed_data"]["title"] == "Backend Engineer"
    assert body["chunk_count"] > 0


def test_delete_job_cascades(client, auth, use_provider, db_session):
    """刪除 → 204、GET 404、chunks / embeddings 連帶清空。"""
    use_provider(_FakeProvider())
    headers = auth()["headers"]

    jid = client.post(
        "/jobs", json={"raw_text": "A job description text."}, headers=headers
    ).json()["id"]
    job_id = uuid.UUID(jid)

    assert client.delete(f"/jobs/{jid}", headers=headers).status_code == 204
    assert client.get(f"/jobs/{jid}", headers=headers).status_code == 404
    assert db_session.scalars(select(JobChunk).where(JobChunk.job_id == job_id)).all() == []
    assert db_session.scalars(select(JobEmbedding).where(JobEmbedding.job_id == job_id)).all() == []


def test_job_user_isolation(client, auth, use_provider):
    """User B 看不到 / 刪不掉 User A 的 job（FR-4 / NFR-8）。"""
    use_provider(_FakeProvider())
    a_headers = auth(email="alice@example.com")["headers"]
    b_headers = auth(email="bob@example.com")["headers"]

    jid = client.post(
        "/jobs", json={"raw_text": "Alice's job description text."}, headers=a_headers
    ).json()["id"]

    assert client.get("/jobs", headers=b_headers).json() == []
    assert client.get(f"/jobs/{jid}", headers=b_headers).status_code == 404
    assert client.delete(f"/jobs/{jid}", headers=b_headers).status_code == 404
    assert client.get(f"/jobs/{jid}", headers=a_headers).status_code == 200


def test_jobs_require_auth(client):
    """未登入存取受保護路由 → 401。"""
    assert client.get("/jobs").status_code == 401
    assert client.post("/jobs", json={"raw_text": "x"}).status_code == 401


def test_create_job_rejects_short_text(client, auth, use_provider):
    """過短文字 → 422（抽取階段擋下，不會呼叫 LLM）。"""
    use_provider(_FakeProvider())
    headers = auth()["headers"]

    resp = client.post("/jobs", json={"raw_text": "hi"}, headers=headers)
    assert resp.status_code == 422


def test_create_job_from_parsed_bypasses_llm_parsing(client, auth, db_session):
    """給定結構化資料 → 不呼叫解析、直接走切塊 + 向量化（Phase 8 seeding 路徑）。

    provider 的 ``generate_structured`` 設為必炸：若 create_job_from_parsed
    仍呼叫解析，這裡會以 parse_status="failed" 現形。
    """
    auth()
    user = db_session.scalars(select(User)).one()
    provider = _FakeProvider(parse_error=StructuredOutputError("parse must not be called"))
    parsed = _sample_job_parsed()

    job = create_job_from_parsed(
        db_session, user=user, raw_text="raw posting text", parsed=parsed, provider=provider
    )

    assert job.parse_status == "parsed"
    assert job.index_status == "indexed"
    assert job.company == "Acme Corp"
    assert job.parsed_data["required_skills"] == ["Python", "FastAPI", "PostgreSQL"]

    expected_chunks = len(chunk_job(parsed))
    chunks = db_session.scalars(
        select(JobChunk).where(JobChunk.job_id == job.id).order_by(JobChunk.chunk_index)
    ).all()
    assert [c.chunk_index for c in chunks] == list(range(expected_chunks))
    embeddings = db_session.scalars(select(JobEmbedding).where(JobEmbedding.job_id == job.id)).all()
    assert len(embeddings) == expected_chunks
