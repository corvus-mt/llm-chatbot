from fastapi.testclient import TestClient

from app.main import app
from app.routes import api as api_module


def test_history_returns_existing_messages() -> None:
    api_module.SESSION_STATE.clear()
    session_id = "session-history"
    api_module.SESSION_STATE[session_id] = {
        "summary": "",
        "messages": [{"role": "user", "content": "Hello."}],
    }

    client = TestClient(app)
    client.cookies.set(api_module.SESSION_COOKIE_NAME, session_id)
    response = client.get("/api/history")

    assert response.status_code == 200
    assert response.json()["messages"] == [{"role": "user", "content": "Hello."}]


def test_clear_resets_history_and_summary() -> None:
    api_module.SESSION_STATE.clear()
    session_id = "session-clear"
    api_module.SESSION_STATE[session_id] = {
        "summary": "Summary.",
        "messages": [{"role": "user", "content": "Hi."}],
        "history": [{"role": "user", "content": "Hi."}],
    }

    client = TestClient(app)
    client.cookies.set(api_module.SESSION_COOKIE_NAME, session_id)
    response = client.post("/api/clear")

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert api_module.SESSION_STATE[session_id]["summary"] == ""
    assert api_module.SESSION_STATE[session_id]["messages"] == []
    assert api_module.SESSION_STATE[session_id]["history"] == []
