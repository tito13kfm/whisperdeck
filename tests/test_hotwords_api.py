def test_list_hotwords_empty_initially(client):
    response = client.get("/api/hotwords")
    assert response.status_code == 200
    assert response.json() == []


def test_add_hotword_via_api(client):
    response = client.post("/api/hotwords", json={"term": "Groq"})
    assert response.status_code == 200
    body = response.json()
    assert body["term"] == "Groq"
    assert body["source"] == "manual"

    listed = client.get("/api/hotwords").json()
    assert len(listed) == 1
    assert listed[0]["term"] == "Groq"


def test_add_hotword_requires_term(client):
    response = client.post("/api/hotwords", json={"term": ""})
    assert response.status_code == 400


def test_delete_hotword_via_api(client):
    created = client.post("/api/hotwords", json={"term": "Groq"}).json()
    response = client.delete(f"/api/hotwords/{created['id']}")
    assert response.status_code == 200
    assert client.get("/api/hotwords").json() == []


def test_delete_missing_hotword_returns_404(client):
    response = client.delete("/api/hotwords/99999")
    assert response.status_code == 404


def test_hotwords_require_login(client):
    client.post("/api/logout")
    response = client.get("/api/hotwords")
    assert response.status_code == 401
