from fastapi.testclient import TestClient

from app.main import app
from app.routes import api as api_module


class DummyResponse:
    def __init__(self, payload: dict) -> None:
        self.status_code = 200
        self._payload = payload
        self.text = "ok"

    def json(self) -> dict:
        return self._payload


class DummyAsyncClient:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    async def __aenter__(self) -> "DummyAsyncClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False

    async def post(self, url: str, params: dict | None = None, json: dict | None = None) -> DummyResponse:
        return DummyResponse(self._payload)


def test_chat_returns_reply(monkeypatch) -> None:
    api_module.settings.llm_api_key = "test-key"
    payload = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "text": "Hello!",
                        }
                    ]
                }
            }
        ]
    }

    monkeypatch.setattr(
        api_module.httpx,
        "AsyncClient",
        lambda timeout=30: DummyAsyncClient(payload),
    )

    client = TestClient(app)
    response = client.post("/api/chat", json={"message": "Say hello in one short sentence."})

    assert response.status_code == 200
    data = response.json()

    assert isinstance(data["reply"], str)
    assert data["reply"].strip()
