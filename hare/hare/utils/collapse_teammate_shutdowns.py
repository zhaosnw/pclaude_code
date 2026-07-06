"""Batch teammate shutdown attachments (`collapseTeammateShutdowns.ts`)."""

from __future__ import annotations

from typing import Any


def _is_teammate_shutdown_attachment(msg: dict[str, Any]) -> bool:
    if msg.get("type") != "attachment":
        return False
    att = msg.get("attachment") or {}
    return (
        att.get("type") == "task_status"
        and att.get("taskType") == "in_process_teammate"
        and att.get("status") == "completed"
    )


def collapse_teammate_shutdowns(messages: list[Any]) -> list[Any]:
    result: list[Any] = []
    i = 0
    while i < len(messages):
        msg = messages[i]
        if _is_teammate_shutdown_attachment(msg):
            count = 0
            while i < len(messages) and _is_teammate_shutdown_attachment(messages[i]):
                count += 1
                i += 1
            if count == 1:
                result.append(msg)
            else:
                result.append(
                    {
                        "type": "attachment",
                        "uuid": msg.get("uuid"),
                        "timestamp": msg.get("timestamp"),
                        "attachment": {
                            "type": "teammate_shutdown_batch",
                            "count": count,
                        },
                    }
                )
        else:
            result.append(msg)
            i += 1
    return result
