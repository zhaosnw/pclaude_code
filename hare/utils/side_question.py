"""Side question /btw (port of sideQuestion.ts)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from hare.utils.forked_agent import CacheSafeParams, ForkedAgentParams, run_forked_agent
from hare.utils.messages import create_user_message, extract_text_content

BTW_PATTERN = re.compile(r"^/btw\b", re.IGNORECASE)


@dataclass
class SideQuestionResult:
    response: str | None
    usage: dict[str, Any]


def find_btw_trigger_positions(
    text: str,
) -> list[dict[str, str | int]]:
    out: list[dict[str, str | int]] = []
    for m in BTW_PATTERN.finditer(text):
        out.append(
            {
                "word": m.group(0),
                "start": m.start(),
                "end": m.end(),
            }
        )
    return out


async def _deny_tool(*_a: Any, **_k: Any) -> dict[str, Any]:
    return {
        "behavior": "deny",
        "message": "Side questions cannot use tools",
        "decision_reason": {"type": "other", "reason": "side_question"},
    }


async def run_side_question(
    *,
    question: str,
    cache_safe_params: CacheSafeParams,
) -> SideQuestionResult:
    wrapped = (
        "<system-reminder>This is a side question from the user. You must answer directly in a single response.\n\n"
        "- You have NO tools available\n"
        "</system-reminder>\n\n"
        f"{question}"
    )
    agent_result = await run_forked_agent(
        ForkedAgentParams(
            prompt_messages=[create_user_message(content=wrapped)],
            cache_safe_params=cache_safe_params,
            can_use_tool=_deny_tool,
            query_source="side_question",
            fork_label="side_question",
            max_turns=1,
            skip_cache_write=True,
        )
    )
    msgs = agent_result.messages
    text = _extract_side_question_response(msgs)
    return SideQuestionResult(response=text, usage=agent_result.total_usage)


def _extract_side_question_response(messages: list[Any]) -> str | None:
    blocks: list[Any] = []
    for m in messages:
        if getattr(m, "type", None) == "assistant":
            blocks.extend(m.message.content)  # type: ignore[attr-defined]
    if blocks:
        t = extract_text_content(blocks, "\n\n").strip()
        if t:
            return t
    return None
