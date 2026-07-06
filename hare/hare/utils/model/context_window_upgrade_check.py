"""Prompt user to upgrade context window when eligible.

Port of: src/utils/model/contextWindowUpgradeCheck.ts
"""

from __future__ import annotations

from typing import Any


async def maybe_prompt_context_window_upgrade(_ctx: dict[str, Any]) -> bool:
    return False
