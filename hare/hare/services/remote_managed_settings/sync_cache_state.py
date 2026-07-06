"""In-memory sync cache state. Port of: src/services/remoteManagedSettings/syncCacheState.ts"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SyncCacheState:
    loaded: bool = False
    etag: str = ""
    data: dict[str, Any] = field(default_factory=dict)
