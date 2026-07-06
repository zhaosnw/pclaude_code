"""
Bridge status utility functions for UI display.

Port of: src/bridge/bridgeStatusUtil.ts
"""

from __future__ import annotations

import time as _time
from typing import Literal, Optional, Union

TOOL_DISPLAY_EXPIRY_MS = 30_000
SHIMMER_INTERVAL_MS = 150

StatusState = Literal["idle", "attached", "titled", "reconnecting", "failed"]


def timestamp() -> str:
    now = _time.localtime()
    return f"{now.tm_hour:02d}:{now.tm_min:02d}:{now.tm_sec:02d}"


def abbreviate_activity(summary: str, max_width: int = 30) -> str:
    return summary[:max_width]


def build_bridge_connect_url(
    environment_id: str, ingress_url: Optional[str] = None
) -> str:
    base = ingress_url or "https://claude.ai"
    return f"{base}/code?bridge={environment_id}"


def build_bridge_session_url(
    session_id: str, environment_id: str, ingress_url: Optional[str] = None
) -> str:
    base = ingress_url or "https://claude.ai"
    return f"{base}/session/{session_id}?bridge={environment_id}"


def compute_glimmer_index(tick: int, message_width: int) -> int:
    cycle_length = message_width + 20
    return message_width + 10 - (tick % cycle_length)


def compute_shimmer_segments(text: str, glimmer_index: int) -> dict[str, str]:
    """Split text into before/shimmer/after for shimmer rendering."""
    text_width = len(text)
    shimmer_start = glimmer_index - 1
    shimmer_end = glimmer_index + 1

    if shimmer_start >= text_width or shimmer_end < 0:
        return {"before": text, "shimmer": "", "after": ""}

    clamped = max(0, shimmer_start)
    before = text[:clamped]
    shimmer = text[clamped : shimmer_end + 1]
    after = text[shimmer_end + 1 :]
    return {"before": before, "shimmer": shimmer, "after": after}


def get_bridge_status(
    error: Optional[str] = None,
    connected: bool = False,
    session_active: bool = False,
    reconnecting: bool = False,
) -> dict[str, str]:
    """Derive a status label and color from bridge connection state."""
    if error:
        return {"label": "Remote Control failed", "color": "error"}
    if reconnecting:
        return {"label": "Remote Control reconnecting", "color": "warning"}
    if session_active or connected:
        return {"label": "Remote Control active", "color": "success"}
    return {"label": "Remote Control connecting…", "color": "warning"}


def build_idle_footer_text(url: str) -> str:
    return f"Code everywhere with the Claude app or {url}"


def build_active_footer_text(url: str) -> str:
    return f"Continue coding in the Claude app or {url}"


FAILED_FOOTER_TEXT = "Something went wrong, please try again"
