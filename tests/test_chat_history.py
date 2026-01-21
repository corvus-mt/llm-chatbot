from fastapi.testclient import TestClient

from app.main import app
from app.routes import api as api_module


def test_history_returns_existing_messages(state_db) -> None:
    api_module._save_state(
        {
            "summary": "",
            "facts": "",
            "messages": [{"role": "user", "content": "Hello."}],
            "history": [{"role": "user", "content": "Hello."}],
        }
    )

    client = TestClient(app)
    response = client.get("/api/history")

    assert response.status_code == 200
    assert response.json()["messages"] == [{"role": "user", "content": "Hello."}]


def test_clear_resets_history_and_summary(state_db) -> None:
    api_module._save_state(
        {
            "summary": "Summary.",
            "facts": "Fact.",
            "messages": [{"role": "user", "content": "Hi."}],
            "history": [{"role": "user", "content": "Hi."}],
        }
    )

    client = TestClient(app)
    response = client.post("/api/clear")

    assert response.status_code == 200
    assert response.json()["ok"] is True
    state = api_module._load_state()
    assert state["summary"] == ""
    assert state["facts"] == ""
    assert state["messages"] == []
    assert state["history"] == []
