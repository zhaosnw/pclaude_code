"""Per-model capability overrides.

Port of: src/utils/model/modelSupportOverrides.ts
"""

from __future__ import annotations

from typing import Any

Overrides = dict[str, dict[str, Any]]

SUPPORT_OVERRIDES: Overrides = {}


def get_support_override(model: str) -> dict[str, Any] | None:
    return SUPPORT_OVERRIDES.get(model)
