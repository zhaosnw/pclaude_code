"""
Session fingerprint for Hare attribution (3-hex prefix).

Port of: src/utils/fingerprint.ts
"""

from __future__ import annotations

import hashlib
from typing import Any, Protocol, runtime_checkable

FINGERPRINT_SALT = "59cf53e54c78"
MACRO_VERSION = "2.1.88"


@runtime_checkable
class _ContentPart(Protocol):
    type: str
    text: str


def extract_first_message_text(messages: list[dict[str, Any]]) -> str:
    for msg in messages:
        if msg.get("type") != "user":
            continue
        inner = msg.get("message", {})
        content = inner.get("content") if isinstance(inner, dict) else None
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    return str(block.get("text", ""))
    return ""


def compute_fingerprint(message_text: str, version: str) -> str:
    indices = (4, 7, 20)
    chars = "".join(message_text[i] if i < len(message_text) else "0" for i in indices)
    fingerprint_input = f"{FINGERPRINT_SALT}{chars}{version}"
    return hashlib.sha256(fingerprint_input.encode()).hexdigest()[:3]


def compute_fingerprint_from_messages(messages: list[dict[str, Any]]) -> str:
    return compute_fingerprint(extract_first_message_text(messages), MACRO_VERSION)
