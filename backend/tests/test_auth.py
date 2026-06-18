PASSWORD = "Sup3rSecret!"


def test_register_success(register_user):
    resp = register_user(email="alice@example.com")
    assert resp.status_code == 201
    body = resp.json()
    assert body["email"] == "alice@example.com"
    assert "id" in body
    # the password must never round-trip back to the client
    assert "password" not in body
    assert "hashed_password" not in body


def test_register_duplicate_email(register_user):
    register_user(email="dup@example.com")
    resp = register_user(email="dup@example.com")
    assert resp.status_code == 409


def test_register_invalid_email(client):
    resp = client.post("/auth/register", json={"email": "not-an-email", "password": PASSWORD})
    assert resp.status_code == 422


def test_register_short_password(client):
    resp = client.post("/auth/register", json={"email": "x@example.com", "password": "short"})
    assert resp.status_code == 422


def test_login_success(client, register_user):
    register_user(email="bob@example.com", password=PASSWORD)
    resp = client.post("/auth/login", json={"email": "bob@example.com", "password": PASSWORD})
    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["token_type"] == "bearer"


def test_login_wrong_password(client, register_user):
    register_user(email="bob@example.com", password=PASSWORD)
    resp = client.post("/auth/login", json={"email": "bob@example.com", "password": "WrongPass1!"})
    assert resp.status_code == 401


def test_login_nonexistent_user(client):
    resp = client.post("/auth/login", json={"email": "ghost@example.com", "password": PASSWORD})
    assert resp.status_code == 401


def test_me_requires_auth(client):
    resp = client.get("/auth/me")
    assert resp.status_code == 401


def test_me_returns_current_user(client, auth):
    session = auth(email="carol@example.com")
    resp = client.get("/auth/me", headers=session["headers"])
    assert resp.status_code == 200
    assert resp.json()["email"] == "carol@example.com"


def test_me_rejects_refresh_token_as_access(client, auth):
    session = auth(email="dave@example.com")
    refresh = session["tokens"]["refresh_token"]
    resp = client.get("/auth/me", headers={"Authorization": f"Bearer {refresh}"})
    assert resp.status_code == 401
