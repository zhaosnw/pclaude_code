"""Subset of `messages.ts` helpers for API/content — port."""

from __future__ import annotations

import re
from typing import Any

INTERRUPT_MESSAGE = "[Request interrupted by user]"
INTERRUPT_MESSAGE_FOR_TOOL_USE = "[Request interrupted by user for tool use]"
CANCEL_MESSAGE = "The user doesn't want to take this action right now. STOP what you are doing and wait for the user to tell you how to proceed."
REJECT_MESSAGE = (
    "The user doesn't want to proceed with this tool use. The tool use was rejected (eg. if it was a file edit, the new_string was NOT written to the file). "
    "STOP what you are doing and wait for the user to tell you how to proceed."
)
NO_RESPONSE_REQUESTED = "No response requested."
SYNTHETIC_TOOL_RESULT_PLACEHOLDER = "[Tool result missing due to internal error]"
SYNTHETIC_MODEL = "<synthetic>"
NO_CONTENT_MESSAGE = "(no content)"

STRIPPED_TAGS_RE = re.compile(
    r"<(commit_analysis|context|function_analysis|pr_analysis)>.*?</\1>\n?", re.DOTALL
)


def strip_prompt_xml_tags(content: str) -> str:
    return STRIPPED_TAGS_RE.sub("", content).strip()


def derive_short_message_id(uuid: str) -> str:
    h = uuid.replace("-", "")[:10]
    n = int(h, 16)
    if n == 0:
        return "0"
    chars = "0123456789abcdefghijklmnopqrstuvwxyz"
    out = []
    while n:
        n, r = divmod(n, 36)
        out.append(chars[r])
    s = "".join(reversed(out))
    return s[:6]


def extract_text_content(blocks: list[dict[str, Any]], separator: str = "") -> str:
    return separator.join(
        b["text"]
        for b in blocks
        if b.get("type") == "text" and isinstance(b.get("text"), str)
    )


def get_content_text(content: str | list[dict[str, Any]] | None) -> str | None:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        t = extract_text_content(content, "\n").strip()
        return t or None
    return None


def extract_tag(html: str, tag_name: str) -> str | None:
    if not html.strip() or not tag_name.strip():
        return None
    esc = re.escape(tag_name)
    pat = re.compile(rf"<{esc}(?:\s[^>]*)?>([\s\S]*?)</{esc}>", re.I)
    m = pat.search(html)
    return m.group(1) if m else None


def auto_reject_message(tool_name: str) -> str:
    return f"Permission to use {tool_name} has been denied."
