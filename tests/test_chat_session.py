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


def test_chat_parses_blocks_without_updating_summary(monkeypatch) -> None:
    api_module.SESSION_STATE.clear()
    session_id = "session-123"
    api_module.SESSION_STATE[session_id] = {
        "summary": "지난 대화 요약",
        "facts": "사용자 이름은 민수다.",
        "messages": [
            {"role": "user", "content": "이전 질문"},
            {"role": "assistant", "content": "이전 답변"},
        ],
    }

    reply_text = "\n".join(
        [
            "[ASSISTANT_REPLY]",
            "- 안녕! 뭐가 궁금해?",
            "",
            "[UPDATED_SUMMARY]",
            "- 아이가 인사를 했다.",
            "- 간단한 안부를 나눴다.",
            "",
            "[UPDATED_FACTS]",
            "- 사용자가 좋아하는 색은 파란색이다.",
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
    client.cookies.set(api_module.SESSION_COOKIE_NAME, session_id)
    response = client.post("/api/chat", json={"message": "오늘 뭐 해?"})

    assert response.status_code == 200
    assert response.json()["reply"] == "안녕! 뭐가 궁금해?"
    assert api_module.SESSION_STATE[session_id]["summary"] == "지난 대화 요약"
    assert api_module.SESSION_STATE[session_id]["facts"] == "사용자 이름은 민수다."
    assert api_module.SESSION_COOKIE_NAME in response.cookies

    sent_prompt = capture["json"]["contents"][0]["parts"][0]["text"]
    assert "[CONVERSATION_SUMMARY]" in sent_prompt
    assert "지난 대화 요약" in sent_prompt
    assert "[CONVERSATION_FACTS]" in sent_prompt
    assert "사용자 이름은 민수다." in sent_prompt
    assert "[RECENT_CONVERSATION]" in sent_prompt
    assert "User: 이전 질문" in sent_prompt
    assert "Assistant: 이전 답변" in sent_prompt
    assert "[USER_MESSAGE]" in sent_prompt
    assert "오늘 뭐 해?" in sent_prompt


def test_chat_falls_back_when_blocks_missing(monkeypatch) -> None:
    api_module.SESSION_STATE.clear()
    session_id = "session-456"
    api_module.SESSION_STATE[session_id] = {
        "summary": "이전 요약",
        "facts": "사용자는 서울에 산다.",
        "messages": [],
    }

    payload = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "text": "그냥 응답",
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
    client.cookies.set(api_module.SESSION_COOKIE_NAME, session_id)
    response = client.post("/api/chat", json={"message": "다시 말해줘"})

    assert response.status_code == 200
    assert response.json()["reply"] == "그냥 응답"
    assert api_module.SESSION_STATE[session_id]["summary"] == "이전 요약"
    assert api_module.SESSION_STATE[session_id]["facts"] == "사용자는 서울에 산다."


def test_chat_updates_summary_when_max_turns_reached(monkeypatch) -> None:
    api_module.SESSION_STATE.clear()
    session_id = "session-789"
    messages = []
    for idx in range(api_module.MAX_DIRECT_TURNS):
        role = "user" if idx % 2 == 0 else "assistant"
        messages.append({"role": role, "content": f"m{idx}"})
    api_module.SESSION_STATE[session_id] = {
        "summary": "Summary.",
        "facts": "Fact.",
        "messages": messages,
    }

    summary_text = "\n".join(
        [
            "[UPDATED_SUMMARY]",
            "- Summary updated.",
            "",
            "[UPDATED_FACTS]",
            "- Fact updated.",
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
    client.cookies.set(api_module.SESSION_COOKIE_NAME, session_id)
    response = client.post("/api/chat", json={"message": "Latest message."})

    assert response.status_code == 200
    assert response.json()["reply"] == "OK."

    summary_prompt = capture[0]["json"]["contents"][0]["parts"][0]["text"]
    assert "[CONVERSATION_SUMMARY]" in summary_prompt
    assert "Summary." in summary_prompt
    assert "[CONVERSATION_FACTS]" in summary_prompt
    assert "Fact." in summary_prompt
    assert "[RECENT_CONVERSATION]" in summary_prompt
    assert "- User: m0" in summary_prompt
    assert "- Assistant: m3" not in summary_prompt
    assert "요약을 업데이트하라." in summary_prompt

    reply_prompt = capture[1]["json"]["contents"][0]["parts"][0]["text"]
    assert "[RECENT_CONVERSATION]" in reply_prompt
    assert "[CONVERSATION_FACTS]" in reply_prompt
    assert "Fact updated." in reply_prompt
    assert "- User: m0" not in reply_prompt
    assert "- Assistant: m3" in reply_prompt

    stored_messages = api_module.SESSION_STATE[session_id]["messages"]
    assert len(stored_messages) == (api_module.MAX_DIRECT_TURNS // 2) + 2
    assert stored_messages[-2]["content"] == "Latest message."
    assert stored_messages[-1]["content"] == "OK."
    assert api_module.SESSION_STATE[session_id]["summary"] == "Summary updated."
    assert api_module.SESSION_STATE[session_id]["facts"] == "Fact updated."
