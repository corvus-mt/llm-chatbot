import json
import logging
from pathlib import Path
from uuid import uuid4

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.config import settings

router = APIRouter()
logger = logging.getLogger(__name__)
raw_logger = logging.getLogger("llm_raw")

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
SESSION_COOKIE_NAME = "kid_chat_session"
ROOT_DIR = Path(__file__).resolve().parents[2]
LOG_DIR = ROOT_DIR / "logs"
RAW_LOG_PATH = LOG_DIR / "llm_raw.log"
SYSTEM_PROMPT_TEXT = (ROOT_DIR / "SYSTEM_PROMPT.md").read_text(encoding="utf-8")
SUMMARY_PROMPT_TEXT = (ROOT_DIR / "SUMMARY_PROMPT.md").read_text(encoding="utf-8")
SESSION_STATE: dict[str, dict] = {}
RECENT_TURNS = 2
MAX_DIRECT_TURNS = RECENT_TURNS * 2
SUMMARY_HEADER = "[CONVERSATION_SUMMARY]"
FACTS_HEADER = "[CONVERSATION_FACTS]"
RECENT_HEADER = "[RECENT_CONVERSATION]"
USER_HEADER = "[USER_MESSAGE]"
REPLY_HEADER = "[ASSISTANT_REPLY]"
UPDATED_SUMMARY_HEADER = "[UPDATED_SUMMARY]"
UPDATED_FACTS_HEADER = "[UPDATED_FACTS]"
SUMMARY_UPDATE_MESSAGE = "요약을 업데이트하라."

if not raw_logger.handlers:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(RAW_LOG_PATH, encoding="utf-8")
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    )
    raw_logger.addHandler(handler)
    raw_logger.setLevel(logging.INFO)
    raw_logger.propagate = False


def _format_bullets(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(f"- {line}" for line in lines)


def _format_dialogue(messages: list[dict]) -> str:
    lines = []
    for message in messages:
        role = message.get("role")
        content = message.get("content", "")
        label = "User" if role == "user" else "Assistant"
        single_line = " ".join(content.split())
        lines.append(f"- {label}: {single_line}")
    return "\n".join(lines)


def _build_prompt(
    user_message: str,
    memory_summary: str | None,
    memory_facts: str | None,
    recent_messages: list[dict],
) -> str:
    blocks = []
    if memory_summary:
        summary_block = _format_bullets(memory_summary)
        if summary_block:
            blocks.append(f"{SUMMARY_HEADER}\n" + summary_block)
    if memory_facts:
        facts_block = _format_bullets(memory_facts)
        if facts_block:
            blocks.append(f"{FACTS_HEADER}\n" + facts_block)
    if recent_messages:
        dialogue_block = _format_dialogue(recent_messages)
        if dialogue_block:
            blocks.append(f"{RECENT_HEADER}\n" + dialogue_block)
    user_block = _format_bullets(user_message)
    blocks.append(f"{USER_HEADER}\n" + user_block)
    input_section = "\n\n".join(blocks)
    return f"{SYSTEM_PROMPT_TEXT}\n\n{input_section}".strip()


def _build_summary_prompt(
    memory_summary: str | None,
    memory_facts: str | None,
    recent_messages: list[dict],
) -> str:
    blocks = []
    if memory_summary:
        summary_block = _format_bullets(memory_summary)
        if summary_block:
            blocks.append(f"{SUMMARY_HEADER}\n" + summary_block)
    if memory_facts:
        facts_block = _format_bullets(memory_facts)
        if facts_block:
            blocks.append(f"{FACTS_HEADER}\n" + facts_block)
    if recent_messages:
        dialogue_block = _format_dialogue(recent_messages)
        if dialogue_block:
            blocks.append(f"{RECENT_HEADER}\n" + dialogue_block)
    instruction_block = _format_bullets(SUMMARY_UPDATE_MESSAGE)
    blocks.append(f"{USER_HEADER}\n" + instruction_block)
    input_section = "\n\n".join(blocks)
    return f"{SUMMARY_PROMPT_TEXT}\n\n{input_section}".strip()


def _normalize_block(text: str) -> str:
    lines = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("-"):
            line = line[1:].strip()
        lines.append(line)
    return "\n".join(lines).strip()


def _extract_blocks(text: str) -> tuple[str, str, str]:
    headers = [REPLY_HEADER, UPDATED_SUMMARY_HEADER, UPDATED_FACTS_HEADER]
    positions = {header: text.find(header) for header in headers}

    def extract(header: str) -> str:
        start = positions[header]
        if start == -1:
            return ""
        start += len(header)
        next_positions = [pos for pos in positions.values() if pos != -1 and pos > start]
        end = min(next_positions) if next_positions else len(text)
        return text[start:end].strip()

    reply_block = _normalize_block(extract(REPLY_HEADER))
    summary_block = _normalize_block(extract(UPDATED_SUMMARY_HEADER))
    facts_block = _normalize_block(extract(UPDATED_FACTS_HEADER))
    return reply_block, summary_block, facts_block


def _log_raw(event: str, payload: dict) -> None:
    raw_logger.info(
        json.dumps(
            {"event": event, **payload},
            ensure_ascii=False,
        )
    )


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)


class ChatResponse(BaseModel):
    reply: str


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatHistoryResponse(BaseModel):
    messages: list[ChatMessage]


class ClearHistoryResponse(BaseModel):
    ok: bool


@router.get("/ping")
def ping() -> dict:
    return {
        "ok": True,
        "message": "pong",
        "persona": {
            "name": settings.persona.name,
            "description": settings.persona.description,
            "tone": settings.persona.tone,
        },
    }


def _get_recent_messages(messages: list[dict]) -> list[dict]:
    if not messages:
        return []
    return list(messages)


def _prune_messages(messages: list[dict]) -> list[dict]:
    if len(messages) <= MAX_DIRECT_TURNS:
        return messages
    return messages[-MAX_DIRECT_TURNS:]


def _ensure_session_state(session_id: str) -> dict:
    if session_id not in SESSION_STATE:
        SESSION_STATE[session_id] = {
            "summary": "",
            "facts": "",
            "messages": [],
            "history": [],
        }
    state = SESSION_STATE[session_id]
    if "summary" not in state:
        state["summary"] = ""
    if "facts" not in state:
        state["facts"] = ""
    if "messages" not in state:
        state["messages"] = []
    if "history" not in state:
        state["history"] = list(state.get("messages", []))
    return state


def _set_session_cookie(response: Response, session_id: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_id,
        httponly=True,
        samesite="lax",
    )


def _get_session(request: Request, response: Response) -> tuple[str, dict]:
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if not session_id:
        session_id = uuid4().hex
    state = _ensure_session_state(session_id)
    _set_session_cookie(response, session_id)
    return session_id, state


@router.get("/history", response_model=ChatHistoryResponse)
def get_history(request: Request, response: Response) -> ChatHistoryResponse:
    _, state = _get_session(request, response)
    history = state.get("history") or []
    return ChatHistoryResponse(messages=history)


@router.post("/clear", response_model=ClearHistoryResponse)
def clear_history(request: Request, response: Response) -> ClearHistoryResponse:
    session_id, state = _get_session(request, response)
    state["summary"] = ""
    state["facts"] = ""
    state["messages"] = []
    state["history"] = []
    SESSION_STATE[session_id] = state
    return ClearHistoryResponse(ok=True)


async def _call_llm(prompt: str, session_id: str, purpose: str) -> str:
    url = GEMINI_URL.format(model=settings.llm_model)
    body = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": prompt}],
            }
        ]
    }
    request_id = uuid4().hex
    _log_raw(
        "llm.request",
        {
            "request_id": request_id,
            "session_id": session_id,
            "provider": settings.llm_provider,
            "model": settings.llm_model,
            "url": url,
            "purpose": purpose,
            "params": {"key": "[REDACTED]"},
            "body": body,
        },
    )

    async with httpx.AsyncClient(timeout=30) as client:
        http_response = await client.post(
            url,
            params={"key": settings.llm_api_key},
            json=body,
        )

    _log_raw(
        "llm.response",
        {
            "request_id": request_id,
            "status_code": http_response.status_code,
            "text": http_response.text,
        },
    )

    if http_response.status_code != 200:
        logger.error("Gemini error %s: %s", http_response.status_code, http_response.text)
        raise HTTPException(
            status_code=502,
            detail=f"Gemini error: {http_response.status_code} {http_response.text}",
        )

    data = http_response.json()
    candidates = data.get("candidates") or []
    if not candidates:
        raise HTTPException(status_code=502, detail="Gemini returned no candidates")

    parts = candidates[0].get("content", {}).get("parts", [])
    reply = ""
    if parts and isinstance(parts, list):
        reply = parts[0].get("text", "")

    if not reply:
        raise HTTPException(status_code=502, detail="Gemini returned empty reply")

    return reply


async def _update_summary_if_needed(state: dict, session_id: str) -> None:
    messages = state.get("messages", [])
    if len(messages) != MAX_DIRECT_TURNS:
        return
    split_index = MAX_DIRECT_TURNS // 2
    summarize_messages = messages[:split_index]
    keep_messages = messages[split_index:MAX_DIRECT_TURNS]

    prompt = _build_summary_prompt(
        state.get("summary", ""),
        state.get("facts", ""),
        summarize_messages,
    )
    reply_text = await _call_llm(prompt, session_id, "summary")
    _, updated_summary, updated_facts = _extract_blocks(reply_text)
    if updated_summary:
        state["summary"] = updated_summary
    if updated_facts:
        state["facts"] = updated_facts
    if not updated_summary and not updated_facts:
        state["summary"] = reply_text.strip()
    state["messages"] = keep_messages


@router.post("/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest, request: Request, response: Response) -> ChatResponse:
    if not settings.llm_api_key:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY is not set")

    session_id, state = _get_session(request, response)
    memory_summary = state.get("summary", "")
    memory_facts = state.get("facts", "")
    await _update_summary_if_needed(state, session_id)
    memory_summary = state.get("summary", "")
    memory_facts = state.get("facts", "")
    recent_messages = _get_recent_messages(state.get("messages", []))

    prompt = _build_prompt(payload.message, memory_summary, memory_facts, recent_messages)
    reply = await _call_llm(prompt, session_id, "chat")

    user_reply, _, _ = _extract_blocks(reply)
    if not user_reply:
        user_reply = reply.strip()
    state_messages = state.get("messages", [])
    history_messages = state.get("history", [])
    new_user_message = {"role": "user", "content": payload.message}
    new_assistant_message = {"role": "assistant", "content": user_reply}
    state_messages.extend([new_user_message, new_assistant_message])
    history_messages.extend([new_user_message, new_assistant_message])
    state["messages"] = _prune_messages(state_messages)
    state["history"] = history_messages

    return ChatResponse(reply=user_reply)
