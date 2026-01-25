import json
import logging
import sqlite3
from pathlib import Path
from uuid import uuid4

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.config import settings

router = APIRouter()
logger = logging.getLogger(__name__)
raw_logger = logging.getLogger("llm_raw")

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
ROOT_DIR = Path(__file__).resolve().parents[2]
LOG_DIR = ROOT_DIR / "logs"
RAW_LOG_PATH = LOG_DIR / "llm_raw.log"
SYSTEM_PROMPT_TEXT = (ROOT_DIR / "SYSTEM_PROMPT.md").read_text(encoding="utf-8")
SUMMARY_PROMPT_TEXT = (ROOT_DIR / "SUMMARY_PROMPT.md").read_text(encoding="utf-8")
STATE_DB_PATH = Path(settings.state_db_path)
STATE_SESSION_ID = "global"
RECENT_TURNS = 2
MAX_DIRECT_TURNS = RECENT_TURNS * 2
SUMMARY_HEADER = "[CONVERSATION_SUMMARY]"
FACTS_HEADER = "[CONVERSATION_FACTS]"
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


def _ensure_state_db() -> None:
    STATE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(STATE_DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                summary TEXT NOT NULL,
                facts TEXT NOT NULL,
                messages TEXT NOT NULL,
                history TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO chat_state (id, summary, facts, messages, history)
            SELECT 1, '', '', '[]', '[]'
            WHERE NOT EXISTS (SELECT 1 FROM chat_state WHERE id = 1)
            """
        )


def _load_state() -> dict:
    _ensure_state_db()
    with sqlite3.connect(STATE_DB_PATH) as conn:
        row = conn.execute(
            "SELECT summary, facts, messages, history FROM chat_state WHERE id = 1"
        ).fetchone()
    if not row:
        return {"summary": "", "facts": "", "messages": [], "history": []}
    summary, facts, messages_raw, history_raw = row
    messages = json.loads(messages_raw) if messages_raw else []
    history = json.loads(history_raw) if history_raw else []
    return {
        "summary": summary or "",
        "facts": facts or "",
        "messages": messages,
        "history": history,
    }


def _save_state(state: dict) -> None:
    _ensure_state_db()
    summary = state.get("summary", "")
    facts = state.get("facts", "")
    messages = json.dumps(state.get("messages", []), ensure_ascii=False)
    history = json.dumps(state.get("history", []), ensure_ascii=False)
    with sqlite3.connect(STATE_DB_PATH) as conn:
        conn.execute(
            """
            UPDATE chat_state
            SET summary = ?, facts = ?, messages = ?, history = ?
            WHERE id = 1
            """,
            (summary, facts, messages, history),
        )


def _reset_state() -> None:
    _save_state({"summary": "", "facts": "", "messages": [], "history": []})


def _format_bullets(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(f"- {line}" for line in lines)


def _build_context_message(
    memory_summary: str | None,
    memory_facts: str | None,
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
    return "\n\n".join(blocks).strip()


def _build_system_instruction(
    base_instruction: str,
    memory_summary: str | None,
    memory_facts: str | None,
) -> str:
    context_message = _build_context_message(memory_summary, memory_facts)
    if not context_message:
        return base_instruction
    return "\n\n".join([base_instruction.rstrip(), context_message]).strip()


def _build_user_message(user_message: str) -> str:
    user_block = _format_bullets(user_message)
    return f"{USER_HEADER}\n" + user_block


def _to_gemini_role(role: str | None) -> str:
    if role == "assistant" or role == "model":
        return "model"
    return "user"


def _build_contents(
    recent_messages: list[dict],
    user_message: str,
) -> list[dict]:
    contents = []
    for message in recent_messages:
        content = message.get("content", "")
        if not content:
            continue
        contents.append(
            {
                "role": _to_gemini_role(message.get("role")),
                "parts": [{"text": content}],
            }
        )
    contents.append(
        {
            "role": "user",
            "parts": [{"text": user_message}],
        }
    )
    return contents


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


@router.get("/history", response_model=ChatHistoryResponse)
def get_history() -> ChatHistoryResponse:
    state = _load_state()
    history = state.get("history") or []
    return ChatHistoryResponse(messages=history)


@router.post("/clear", response_model=ClearHistoryResponse)
def clear_history() -> ClearHistoryResponse:
    _reset_state()
    return ClearHistoryResponse(ok=True)


async def _call_llm(
    system_instruction: str | None,
    contents: list[dict],
    purpose: str,
) -> str:
    url = GEMINI_URL.format(model=settings.llm_model)
    body = {"contents": contents}
    if system_instruction:
        body["systemInstruction"] = {
            "parts": [{"text": system_instruction}],
        }
    request_id = uuid4().hex
    _log_raw(
        "llm.request",
        {
            "request_id": request_id,
            "session_id": STATE_SESSION_ID,
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


async def _update_summary_if_needed(state: dict) -> None:
    messages = state.get("messages", [])
    if len(messages) != MAX_DIRECT_TURNS:
        return
    split_index = MAX_DIRECT_TURNS // 2
    summarize_messages = messages[:split_index]
    keep_messages = messages[split_index:MAX_DIRECT_TURNS]

    user_message = _build_user_message(SUMMARY_UPDATE_MESSAGE)
    contents = _build_contents(
        summarize_messages,
        user_message,
    )
    system_instruction = _build_system_instruction(
        SUMMARY_PROMPT_TEXT,
        state.get("summary", ""),
        state.get("facts", ""),
    )
    reply_text = await _call_llm(system_instruction, contents, "summary")
    _, updated_summary, updated_facts = _extract_blocks(reply_text)
    if updated_summary:
        state["summary"] = updated_summary
    if updated_facts:
        state["facts"] = updated_facts
    if not updated_summary and not updated_facts:
        state["summary"] = reply_text.strip()
    state["messages"] = keep_messages


@router.post("/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest) -> ChatResponse:
    if not settings.llm_api_key:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY is not set")

    state = _load_state()
    memory_summary = state.get("summary", "")
    memory_facts = state.get("facts", "")
    await _update_summary_if_needed(state)
    memory_summary = state.get("summary", "")
    memory_facts = state.get("facts", "")
    recent_messages = _get_recent_messages(state.get("messages", []))

    user_message = _build_user_message(payload.message)
    contents = _build_contents(
        recent_messages,
        user_message,
    )
    system_instruction = _build_system_instruction(
        SYSTEM_PROMPT_TEXT,
        memory_summary,
        memory_facts,
    )
    reply = await _call_llm(system_instruction, contents, "chat")

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
    _save_state(state)

    return ChatResponse(reply=user_reply)
