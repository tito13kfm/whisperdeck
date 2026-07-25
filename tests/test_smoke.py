def test_health_check_unauthenticated(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_client_fixture_is_authenticated(client):
    response = client.get("/api/me")
    assert response.status_code == 200
    assert response.json()["username"] == "testuser"


def test_favicon_returns_204(client):
    """Fresh page loads silence favicon console noise — always 204, no 404."""
    response = client.get("/favicon.ico")
    assert response.status_code == 204
