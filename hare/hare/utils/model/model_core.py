"""Core model resolution and defaults. Port of model/model.ts."""

from __future__ import annotations

from typing import Any


def get_default_model(_settings: dict[str, Any] | None = None) -> str:
    return "hare-3-5-sonnet-latest"
