"""Agent-type hook execution.

Port of: src/utils/hooks/execAgentHook.ts
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class AgentHookOutcome:
    stdout: str
    stderr: str
    exit_code: int


async def exec_agent_hook(
    prompt: str,
    *,
    model: str | None = None,
    context: dict[str, Any] | None = None,
) -> AgentHookOutcome:
    del prompt, model, context
    return AgentHookOutcome(stdout="", stderr="agent hook not wired", exit_code=1)
