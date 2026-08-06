import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.db import models  # noqa: F401  (registers tables on Base.metadata)
from app.db.session import Base, get_db
from app.main import app

# 獨立測試 DB（Phase 9）：預設 careerpilot_test，跑 pytest 不再 TRUNCATE 開發
# 資料庫。刻意不 fallback 到 DATABASE_URL——只認 TEST_DATABASE_URL 覆寫。
TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql://careerpilot:careerpilot@localhost:5432/careerpilot_test",
)

engine = create_engine(TEST_DATABASE_URL)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

DEFAULT_PASSWORD = "Sup3rSecret!"


def _ensure_test_database() -> None:
    """確保測試 DB 存在（含 pgvector）——同 eval/harness.ensure_database 模式：
    程式化建立而非 compose init script（init script 在既有 volume 不會執行）。"""
    url = make_url(TEST_DATABASE_URL)
    maintenance = url.set(database="postgres")
    try:
        admin_engine = create_engine(maintenance, isolation_level="AUTOCOMMIT")
        with admin_engine.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": url.database},
            ).scalar()
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{url.database}"'))
        admin_engine.dispose()
    except Exception as exc:
        raise RuntimeError(
            f"cannot reach Postgres at {maintenance.render_as_string(hide_password=True)} — "
            f"start it with `docker compose up -d db` ({exc})"
        ) from exc
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))


@pytest.fixture(scope="session", autouse=True)
def _create_schema():
    """Ensure the test DB + tables exist; idempotent after `alembic upgrade head`."""
    _ensure_test_database()
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture(autouse=True)
def _disable_rate_limits():
    """測試以同一 IP（testclient）高速打 API，預設關閉限流；
    test_rate_limit.py 內自行重新啟用。slowapi 於 request 時檢查 enabled，
    不需要 env / import 順序技巧。"""
    from app.core.ratelimit import limiter

    previous = limiter.enabled
    limiter.enabled = False
    yield
    limiter.enabled = previous


@pytest.fixture(autouse=True)
def _clean_tables():
    """Truncate auth tables after each test so cases stay isolated."""
    yield
    with engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE users, refresh_tokens, llm_call_logs, resumes, resume_versions, "
                "resume_embeddings, jobs, job_chunks, job_embeddings, match_results, "
                "skill_gap_reports, generated_artifacts RESTART IDENTITY CASCADE"
            )
        )


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def db_session():
    """Direct DB session for seeding/asserting at the data layer."""
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def client():
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def register_user(client):
    """Factory: POST /auth/register and return the raw response."""

    def _register(
        email: str = "user@example.com",
        password: str = DEFAULT_PASSWORD,
        full_name: str | None = "Test User",
    ):
        return client.post(
            "/auth/register",
            json={"email": email, "password": password, "full_name": full_name},
        )

    return _register


@pytest.fixture
def auth(client):
    """Factory: register + login, returning the token pair and auth headers."""

    def _auth(email: str = "user@example.com", password: str = DEFAULT_PASSWORD):
        client.post(
            "/auth/register",
            json={"email": email, "password": password, "full_name": "Test User"},
        )
        resp = client.post("/auth/login", json={"email": email, "password": password})
        tokens = resp.json()
        return {
            "tokens": tokens,
            "headers": {"Authorization": f"Bearer {tokens['access_token']}"},
        }

    return _auth
