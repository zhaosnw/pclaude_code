"""AI-generated session titles (port of sessionTitle.ts)."""

from __future__ import annotations

import json
from typing import Any

from hare.bootstrap import state as bootstrap_state
from hare.utils.debug import log_for_debugging
from hare.utils.messages import extract_text_content
from hare.utils.system_prompt_type import as_system_prompt

MAX_CONVERSATION_TEXT = 1000

SESSION_TITLE_PROMPT = """Generate a concise, sentence-case title (3-7 words) that captures the main topic or goal of this coding session. The title should be clear enough that the user recognizes the session in a list. Use sentence case: capitalize only the first word and proper nouns.

Return JSON with a single "title" field.

Good examples:
{"title": "Fix login button on mobile"}
{"title": "Add OAuth authentication"}
{"title": "Debug failing CI tests"}
{"title": "Refactor API client error handling"}

Bad (too vague): {"title": "Code changes"}
Bad (too long): {"title": "Investigate and fix the issue where the login button does not respond on mobile devices"}
Bad (wrong case): {"title": "Fix Login Button On Mobile"}"""


def extract_conversation_text(messages: list[Any]) -> str:
    parts: list[str] = []
    for msg in messages:
        mtype = getattr(msg, "type", None)
        if mtype not in ("user", "assistant"):
            continue
        if getattr(msg, "is_meta", False):
            continue
        origin = getattr(msg, "origin", None)
        if origin is not None and getattr(origin, "kind", None) != "human":
            continue
        content = msg.message.content  # type: ignore[attr-defined]
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(str(block.get("text", "")))
    text = "\n".join(parts)
    if len(text) > MAX_CONVERSATION_TEXT:
        return text[-MAX_CONVERSATION_TEXT:]
    return text


async def generate_session_title(description: str, signal: Any) -> str | None:
    trimmed = description.strip()
    if not trimmed:
        return None

    try:
        from hare.services.api import claude as hare_api

        qh = getattr(hare_api, "query_haiku", None)
        if qh is None:
            log_for_debugging("query_haiku not available", level="error")
            return None

        result = await qh(
            system_prompt=as_system_prompt(SESSION_TITLE_PROMPT),
            user_prompt=trimmed,
            output_format={
                "type": "json_schema",
                "schema": {
                    "type": "object",
                    "properties": {"title": {"type": "string"}},
                    "required": ["title"],
                    "additionalProperties": False,
                },
            },
            signal=signal,
            options={
                "query_source": "generate_session_title",
                "agents": [],
                "is_non_interactive_session": bootstrap_state.get_is_non_interactive_session(),
                "has_append_system_prompt": False,
                "mcp_tools": [],
            },
        )
        text = extract_text_content(result.message.content)
        parsed = json.loads(text) if text else {}
        title = str(parsed.get("title", "")).strip() or None

        try:
            from hare.services.analytics import log_event

            log_event("tengu_session_title_generated", {"success": title is not None})
        except ImportError:
            pass

        return title
    except Exception as e:
        log_for_debugging(f"generateSessionTitle failed: {e}", level="error")
        try:
            from hare.services.analytics import log_event

            log_event("tengu_session_title_generated", {"success": False})
        except ImportError:
            pass
        return None
