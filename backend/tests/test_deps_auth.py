"""get_current_user 的防禦分支（Phase 9 補死角）：壞 JWT、缺 sub、非 UUID
sub、停用帳號——全部應回 401，不洩漏原因細節。"""

import time

from jose import jwt

from app.core.config import settings
from app.db.models import User


def _make_token(payload: dict) -> str:
    now = int(time.time())
    full = {"iat": now, "exp": now + 600, **payload}
    return jwt.encode(full, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def test_malformed_jwt_rejected(client):
    resp = client.get("/auth/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert resp.status_code == 401


def test_token_missing_sub_rejected(client):
    token = _make_token({"type": "access"})
    resp = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


def test_token_non_uuid_sub_rejected(client):
    token = _make_token({"type": "access", "sub": "definitely-not-a-uuid"})
    resp = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


def test_inactive_user_rejected(client, auth, db_session):
    """帳號被停用後，先前簽發的 access token 立即失效。"""
    session = auth(email="inactive@example.com")
    user = db_session.query(User).filter_by(email="inactive@example.com").one()
    user.is_active = False
    db_session.commit()

    resp = client.get("/auth/me", headers=session["headers"])
    assert resp.status_code == 401
