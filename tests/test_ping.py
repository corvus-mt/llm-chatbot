from fastapi.testclient import TestClient

from app.main import app


def test_ping_returns_persona() -> None:
    client = TestClient(app)
    response = client.get("/api/ping")

    assert response.status_code == 200
    data = response.json()

    assert data["ok"] is True
    assert data["message"] == "pong"
    assert data["persona"]["name"]
    assert data["persona"]["description"]
    assert data["persona"]["tone"]
