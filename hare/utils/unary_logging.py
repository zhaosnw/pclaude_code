"""Unary completion analytics (port of unaryLogging.ts)."""

from __future__ import annotations

from typing import Any, Literal

CompletionType = Literal[
    "str_replace_single",
    "str_replace_multi",
    "write_file_single",
    "tool_use_single",
]


async def log_unary_event(
    *,
    completion_type: CompletionType,
    event: Literal["accept", "reject", "response"],
    metadata: dict[str, Any],
) -> None:
    lang = metadata.get("language_name")
    if hasattr(lang, "__await__"):
        lang = await lang  # type: ignore[assignment]
    try:
        from hare.services.analytics import log_event

        log_event(
            "tengu_unary_event",
            {
                "event": event,
                "completion_type": completion_type,
                "language_name": lang,
                "message_id": metadata.get("message_id"),
                "platform": metadata.get("platform"),
                **(
                    {"hasFeedback": metadata["hasFeedback"]}
                    if "hasFeedback" in metadata
                    else {}
                ),
            },
        )
    except ImportError:
        pass
