"""Thinking / ultrathink config helpers (port of thinking.ts)."""

from __future__ import annotations

import re
from typing import Literal

ThinkingConfig = (
    dict[Literal["type"], Literal["adaptive"]]
    | dict[Literal["type", "budget_tokens"], Literal["enabled"] | int]
    | dict[Literal["type"], Literal["disabled"]]
)


def is_ultrathink_enabled() -> bool:
    try:
        from hare.services.analytics.growthbook import (
            get_feature_value_cached_may_be_stale,
        )

        return bool(get_feature_value_cached_may_be_stale("tengu_turtle_carbon", True))
    except ImportError:
        return False


def has_ultrathink_keyword(text: str) -> bool:
    return bool(re.search(r"\bultrathink\b", text, re.IGNORECASE))


def find_thinking_trigger_positions(
    text: str,
) -> list[dict[str, str | int]]:
    out: list[dict[str, str | int]] = []
    for m in re.finditer(r"\bultrathink\b", text, re.IGNORECASE):
        out.append({"word": m.group(0), "start": m.start(), "end": m.end()})
    return out


def get_rainbow_color(char_index: int, shimmer: bool = False) -> str:
    colors = (
        [
            "rainbow_red_shimmer",
            "rainbow_orange_shimmer",
            "rainbow_yellow_shimmer",
            "rainbow_green_shimmer",
            "rainbow_blue_shimmer",
            "rainbow_indigo_shimmer",
            "rainbow_violet_shimmer",
        ]
        if shimmer
        else [
            "rainbow_red",
            "rainbow_orange",
            "rainbow_yellow",
            "rainbow_green",
            "rainbow_blue",
            "rainbow_indigo",
            "rainbow_violet",
        ]
    )
    return colors[char_index % len(colors)]


def model_supports_thinking(model: str) -> bool:
    _ = model
    return True


def model_supports_adaptive_thinking(model: str) -> bool:
    m = model.lower()
    return "opus-4-6" in m or "sonnet-4-6" in m
