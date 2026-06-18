def test_refresh_returns_working_tokens(client, auth):
    session = auth(email="rita@example.com")
    refresh = session["tokens"]["refresh_token"]

    resp = client.post("/auth/refresh", json={"refresh_token": refresh})
    assert resp.status_code == 200
    new_tokens = resp.json()
    assert new_tokens["access_token"]
    assert new_tokens["refresh_token"]

    me = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {new_tokens['access_token']}"},
    )
    assert me.status_code == 200
    assert me.json()["email"] == "rita@example.com"


def test_old_refresh_token_revoked_after_rotation(client, auth):
    session = auth(email="sam@example.com")
    refresh = session["tokens"]["refresh_token"]

    first = client.post("/auth/refresh", json={"refresh_token": refresh})
    assert first.status_code == 200

    # reusing the rotated-out refresh token must fail
    second = client.post("/auth/refresh", json={"refresh_token": refresh})
    assert second.status_code == 401


def test_logout_revokes_refresh_token(client, auth):
    session = auth(email="tina@example.com")
    refresh = session["tokens"]["refresh_token"]

    logout = client.post("/auth/logout", json={"refresh_token": refresh})
    assert logout.status_code == 204

    resp = client.post("/auth/refresh", json={"refresh_token": refresh})
    assert resp.status_code == 401
