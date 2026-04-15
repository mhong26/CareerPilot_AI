def test_health_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["db"] == "ok"


def test_health_pgvector(client):
    response = client.get("/health")
    data = response.json()
    # pgvector is enabled via migration 0001; "not_installed" is also acceptable
    # if migrations haven't been run yet
    assert data["pgvector"] in ("ok", "not_installed")
