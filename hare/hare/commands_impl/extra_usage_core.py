"""
Core extra-usage reporting (shared by TTY and CI).

Port of: src/commands/extra-usage/extra-usage-core.ts
"""

from __future__ import annotations

from typing import Any


async def collect_extra_usage_report() -> dict[str, Any]:
    return {"lines": []}
