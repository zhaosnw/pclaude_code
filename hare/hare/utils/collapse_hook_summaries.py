"""Merge consecutive stop-hook summary messages (`collapseHookSummaries.ts`)."""

from __future__ import annotations

from typing import Any


def _is_labeled_hook_summary(msg: dict[str, Any]) -> bool:
    return (
        msg.get("type") == "system"
        and msg.get("subtype") == "stop_hook_summary"
        and msg.get("hookLabel") is not None
    )


def collapse_hook_summaries(messages: list[Any]) -> list[Any]:
    result: list[Any] = []
    i = 0
    while i < len(messages):
        msg = messages[i]
        if _is_labeled_hook_summary(msg):
            label = msg.get("hookLabel")
            group: list[dict[str, Any]] = []
            while (
                i < len(messages)
                and _is_labeled_hook_summary(messages[i])
                and messages[i].get("hookLabel") == label
            ):
                group.append(messages[i])
                i += 1
            if len(group) == 1:
                result.append(msg)
            else:
                merged = {
                    **msg,
                    "hookCount": sum(m.get("hookCount", 0) for m in group),
                    "hookInfos": [x for m in group for x in (m.get("hookInfos") or [])],
                    "hookErrors": [
                        x for m in group for x in (m.get("hookErrors") or [])
                    ],
                    "preventedContinuation": any(
                        m.get("preventedContinuation") for m in group
                    ),
                    "hasOutput": any(m.get("hasOutput") for m in group),
                    "totalDurationMs": max(
                        (m.get("totalDurationMs") or 0) for m in group
                    ),
                }
                result.append(merged)
        else:
            result.append(msg)
            i += 1
    return result
