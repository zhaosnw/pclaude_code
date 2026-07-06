"""Team memory sync DTOs. Port of: src/services/teamMemorySync/types.ts"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TeamMemorySyncState:
    last_sync_revision: str = ""
    pending_paths: list[str] = field(default_factory=list)
