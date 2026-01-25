import json

from fastapi.testclient import TestClient

from app.main import app
from app.routes import api as api_module


class DummyResponse:
    def __init__(self, payload: dict) -> None:
        self.status_code = 200
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self) -> dict:
        return self._payload


class DummyAsyncClient:
    def __init__(self, payload: dict, capture: dict) -> None:
        self._payload = payload
        self._capture = capture

    async def __aenter__(self) -> "DummyAsyncClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False

    async def post(self, url: str, params: dict | None = None, json: dict | None = None) -> DummyResponse:
        self._capture["url"] = url
        self._capture["params"] = params
        self._capture["json"] = json
        return DummyResponse(self._payload)


class DummySequenceAsyncClient:
    def __init__(self, payloads: list[dict], capture: list[dict]) -> None:
        self._payloads = list(payloads)
        self._capture = capture

    async def __aenter__(self) -> "DummySequenceAsyncClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False

    async def post(self, url: str, params: dict | None = None, json: dict | None = None) -> DummyResponse:
        payload = self._payloads.pop(0)
        self._capture.append({"url": url, "params": params, "json": json})
        return DummyResponse(payload)


def test_chat_parses_blocks_without_updating_summary(monkeypatch, state_db) -> None:
    api_module._save_state(
        {
            "summary": "Previous summary",
            "facts": "User name is Minsoo.",
            "messages": [
                {"role": "user", "content": "Previous question"},
                {"role": "assistant", "content": "Previous answer"},
            ],
            "history": [
                {"role": "user", "content": "Previous question"},
                {"role": "assistant", "content": "Previous answer"},
            ],
        }
    )

    reply_text = "\n".join(
        [
            "[ASSISTANT_REPLY]",
            "- Hello! What can I help with?",
            "",
            "[UPDATED_SUMMARY]",
            "- Summary refreshed.",
            "",
            "[UPDATED_FACTS]",
            "- Likes apples.",
        ]
    )
    payload = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "text": reply_text,
                        }
                    ]
                }
            }
        ]
    }
    capture: dict = {}

    monkeypatch.setattr(
        api_module.httpx,
        "AsyncClient",
        lambda timeout=30: DummyAsyncClient(payload, capture),
    )

    client = TestClient(app)
    response = client.post("/api/chat", json={"message": "What should I do today?"})

    assert response.status_code == 200
    assert response.json()["reply"] == "Hello! What can I help with?"
    state = api_module._load_state()
    assert state["summary"] == "Previous summary"
    assert state["facts"] == "User name is Minsoo."

    system_text = capture["json"]["systemInstruction"]["parts"][0]["text"]
    assert api_module.SYSTEM_PROMPT_TEXT in system_text
    assert "[CONVERSATION_SUMMARY]" in system_text
    assert "Previous summary" in system_text
    assert "[CONVERSATION_FACTS]" in system_text
    assert "User name is Minsoo." in system_text

    assert capture["json"]["contents"][0]["role"] == "user"
    assert capture["json"]["contents"][0]["parts"][0]["text"] == "Previous question"
    assert capture["json"]["contents"][1]["role"] == "model"
    assert capture["json"]["contents"][1]["parts"][0]["text"] == "Previous answer"

    last_text = capture["json"]["contents"][-1]["parts"][0]["text"]
    assert last_text == "What should I do today?"


def test_chat_falls_back_when_blocks_missing(monkeypatch, state_db) -> None:
    api_module._save_state(
        {
            "summary": "Earlier summary",
            "facts": "User lives in Seoul.",
            "messages": [],
            "history": [],
        }
    )

    payload = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "text": "Plain reply",
                        }
                    ]
                }
            }
        ]
    }

    monkeypatch.setattr(
        api_module.httpx,
        "AsyncClient",
        lambda timeout=30: DummyAsyncClient(payload, {}),
    )

    client = TestClient(app)
    response = client.post("/api/chat", json={"message": "Say it again"})

    assert response.status_code == 200
    assert response.json()["reply"] == "Plain reply"
    state = api_module._load_state()
    assert state["summary"] == "Earlier summary"
    assert state["facts"] == "User lives in Seoul."


def test_chat_updates_summary_when_max_turns_reached(monkeypatch, state_db) -> None:
    messages = []
    for idx in range(api_module.MAX_DIRECT_TURNS):
        role = "user" if idx % 2 == 0 else "assistant"
        messages.append({"role": role, "content": f"m{idx}"})
    api_module._save_state(
        {
            "summary": "Summary.",
            "facts": "Fact.",
            "messages": messages,
            "history": list(messages),
        }
    )

    summary_text = "\n".join(
        [
            "[UPDATED_SUMMARY]",
            "- Summary updated.",
            "",
            "[UPDATED_FACTS]",
            "- Fact updated.",
            "",
            "[CONVERSATION_SUMMARY]",
            "- This should be ignored.",
            "",
            "[CONVERSATION_FACTS]",
            "- Also ignored.",
        ]
    )
    reply_text = "\n".join(["[ASSISTANT_REPLY]", "- OK."])
    payloads = [
        {"candidates": [{"content": {"parts": [{"text": summary_text}]}}]},
        {"candidates": [{"content": {"parts": [{"text": reply_text}]}}]},
    ]
    capture: list[dict] = []

    sequence_client = DummySequenceAsyncClient(payloads, capture)

    monkeypatch.setattr(
        api_module.httpx,
        "AsyncClient",
        lambda timeout=30: sequence_client,
    )

    client = TestClient(app)
    response = client.post("/api/chat", json={"message": "Latest message."})

    assert response.status_code == 200
    assert response.json()["reply"] == "OK."

    summary_system = capture[0]["json"]["systemInstruction"]["parts"][0]["text"]
    assert api_module.SUMMARY_PROMPT_TEXT in summary_system
    assert "[CONVERSATION_SUMMARY]" in summary_system
    assert "Summary." in summary_system
    assert "[CONVERSATION_FACTS]" in summary_system
    assert "Fact." in summary_system
    assert capture[0]["json"]["contents"][-1]["parts"][0]["text"] == api_module.SUMMARY_UPDATE_MESSAGE

    assert capture[0]["json"]["contents"][0]["role"] == "user"
    assert capture[0]["json"]["contents"][0]["parts"][0]["text"] == "m0"
    assert capture[0]["json"]["contents"][-1]["role"] == "user"
    assert capture[0]["json"]["contents"][-1]["parts"][0]["text"] == api_module.SUMMARY_UPDATE_MESSAGE

    reply_system = capture[1]["json"]["systemInstruction"]["parts"][0]["text"]
    assert api_module.SYSTEM_PROMPT_TEXT in reply_system
    assert "[CONVERSATION_FACTS]" in reply_system
    assert "Fact updated." in reply_system
    assert capture[1]["json"]["contents"][0]["parts"][0]["text"] == "m2"
    assert capture[1]["json"]["contents"][1]["parts"][0]["text"] == "m3"
    assert capture[1]["json"]["contents"][-1]["parts"][0]["text"] == "Latest message."

    state = api_module._load_state()
    stored_messages = state["messages"]
    assert len(stored_messages) == (api_module.MAX_DIRECT_TURNS // 2) + 2
    assert stored_messages[-2]["content"] == "Latest message."
    assert stored_messages[-1]["content"] == "OK."
    assert state["summary"] == "Summary updated."
    assert state["facts"] == "Fact updated."
    assert reply_system.count("[CONVERSATION_SUMMARY]") == 1
    assert reply_system.count("[CONVERSATION_FACTS]") == 1
