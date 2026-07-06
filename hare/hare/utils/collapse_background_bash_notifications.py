"""Collapse consecutive background-bash completion notifications."""

from __future__ import annotations

import re
from typing import Any

from hare.utils.fullscreen import is_fullscreen_env_enabled

TASK_NOTIFICATION_TAG = "task-notification"
STATUS_TAG = "status"
SUMMARY_TAG = "summary"
BACKGROUND_BASH_SUMMARY_PREFIX = "Background command "


def _extract_tag(text: str, tag: str) -> str | None:
    m = re.search(rf"<{tag}>([^<]*)</{tag}>", text)
    return m.group(1) if m else None


def _is_completed_background_bash(msg: dict[str, Any]) -> bool:
    if msg.get("type") != "user":
        return False
    content = (msg.get("message") or {}).get("content") or []
    if not content or content[0].get("type") != "text":
        return False
    text = content[0].get("text") or ""
    if f"<{TASK_NOTIFICATION_TAG}" not in text:
        return False
    if _extract_tag(text, STATUS_TAG) != "completed":
        return False
    summary = _extract_tag(text, SUMMARY_TAG) or ""
    return summary.startswith(BACKGROUND_BASH_SUMMARY_PREFIX)


def collapse_background_bash_notifications(
    messages: list[Any],
    verbose: bool,
) -> list[Any]:
    if not is_fullscreen_env_enabled():
        return messages
    if verbose:
        return messages

    result: list[Any] = []
    i = 0
    while i < len(messages):
        msg = messages[i]
        if _is_completed_background_bash(msg):
            count = 0
            while i < len(messages) and _is_completed_background_bash(messages[i]):
                count += 1
                i += 1
            if count == 1:
                result.append(msg)
            else:
                synth = {
                    **msg,
                    "message": {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    f"<{TASK_NOTIFICATION_TAG}>"
                                    f"<{STATUS_TAG}>completed</{STATUS_TAG}>"
                                    f"<{SUMMARY_TAG}>{count} background commands completed</{SUMMARY_TAG}>"
                                    f"</{TASK_NOTIFICATION_TAG}>"
                                ),
                            }
                        ],
                    },
                }
                result.append(synth)
        else:
            result.append(msg)
            i += 1
    return result
