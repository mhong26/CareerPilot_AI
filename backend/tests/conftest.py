import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.db import models  # noqa: F401  (registers tables on Base.metadata)
from app.db.session import Base, get_db
from app.main import app

TEST_DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://careerpilot:careerpilot@localhost:5432/careerpilot",
)

engine = create_engine(TEST_DATABASE_URL)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

DEFAULT_PASSWORD = "Sup3rSecret!"


@pytest.fixture(scope="session", autouse=True)
def _create_schema():
    """Ensure tables exist; idempotent no-op after `alembic upgrade head` in CI."""
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture(autouse=True)
def _clean_tables():
    """Truncate auth tables after each test so cases stay isolated."""
    yield
    with engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE users, refresh_tokens, llm_call_logs, resumes, resume_versions "
                "RESTART IDENTITY CASCADE"
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
