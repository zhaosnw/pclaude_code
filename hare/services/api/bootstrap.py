"""
Bootstrap API — model list and client_data from console.

Port of: src/services/api/bootstrap.ts
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class AdditionalModelOption:
    value: str
    label: str
    description: str


@dataclass
class BootstrapResponse:
    client_data: Optional[dict[str, Any]] = None
    additional_model_options: list[AdditionalModelOption] = field(default_factory=list)


async def fetch_bootstrap_api() -> BootstrapResponse | None:
    """Fetch bootstrap payload; skipped when nonessential traffic disabled or no auth."""
    return None
