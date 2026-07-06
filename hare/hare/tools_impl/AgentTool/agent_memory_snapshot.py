"""Snapshot agent memory for replay/debug. Port of: src/tools/AgentTool/agentMemorySnapshot.ts"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentMemorySnapshot:
    agent_id: str
    turns: list[dict[str, Any]] = field(default_factory=list)
