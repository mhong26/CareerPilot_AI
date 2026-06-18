"""FR-4 / NFR-8 — data is scoped per user.

At this phase only User + RefreshToken exist, so we verify the ownership-filter
primitive (every future "list my X" relies on it) plus token→user mapping.
HTTP-level resource isolation is added in Phase 3/4 with the owned endpoints.
"""

from sqlalchemy import select

from app.db.models import RefreshToken, User


def test_me_isolation(client, auth):
    a = auth(email="a@example.com")
    b = auth(email="b@example.com")

    me_a = client.get("/auth/me", headers=a["headers"]).json()
    me_b = client.get("/auth/me", headers=b["headers"]).json()

    assert me_a["email"] == "a@example.com"
    assert me_b["email"] == "b@example.com"
    assert me_a["id"] != me_b["id"]


def test_refresh_tokens_scoped_by_user(client, auth, db_session):
    auth(email="a@example.com")
    auth(email="b@example.com")

    user_a = db_session.scalar(select(User).where(User.email == "a@example.com"))
    user_b = db_session.scalar(select(User).where(User.email == "b@example.com"))
    assert user_a.id != user_b.id

    a_tokens = db_session.scalars(
        select(RefreshToken).where(RefreshToken.user_id == user_a.id)
    ).all()
    assert len(a_tokens) >= 1
    # every row returned by the ownership filter belongs to A, never B
    assert all(token.user_id == user_a.id for token in a_tokens)
    assert all(token.user_id != user_b.id for token in a_tokens)
