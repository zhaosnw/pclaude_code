"""Remote managed settings DTOs. Port of: src/services/remoteManagedSettings/types.ts"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RemoteManagedSettingsPayload:
    version: int = 0
    settings: dict[str, Any] = field(default_factory=dict)
