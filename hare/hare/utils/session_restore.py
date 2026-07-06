"""Resume / continue session orchestration (port of sessionRestore.ts)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ResumeResult:
    messages: list[Any] | None = None
    file_history_snapshots: list[Any] | None = None
    attribution_snapshots: list[Any] | None = None
    context_collapse_commits: list[Any] | None = None
    context_collapse_snapshot: Any | None = None


def restore_session_state_from_log(
    result: ResumeResult,
    set_app_state: Any,
) -> None:
    _ = (result, set_app_state)


def compute_restored_attribution_state(result: ResumeResult) -> Any | None:
    _ = result
    return None


def compute_standalone_agent_context(
    agent_name: str | None,
    agent_color: str | None,
) -> dict[str, Any] | None:
    if not agent_name and not agent_color:
        return None
    return {
        "name": agent_name or "",
        "color": None if agent_color == "default" else agent_color,
    }


def restore_agent_from_session(
    agent_setting: str | None,
    current_agent_definition: Any | None,
    agent_definitions: Any,
) -> tuple[Any | None, str | None]:
    _ = agent_definitions
    if current_agent_definition:
        return current_agent_definition, None
    if not agent_setting:
        return None, None
    return None, None


async def refresh_agent_definitions_for_mode_switch(
    mode_was_switched: bool,
    current_cwd: str,
    cli_agents: list[Any],
    current_agent_definitions: Any,
) -> Any:
    _ = (mode_was_switched, current_cwd, cli_agents)
    return current_agent_definitions


def restore_worktree_for_resume(worktree_session: Any | None) -> None:
    _ = worktree_session


def exit_restored_worktree() -> None:
    pass


async def process_resumed_conversation(
    result: Any,
    opts: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    _ = (opts, context)
    return {
        "messages": getattr(result, "messages", []),
        "initial_state": context.get("initial_state"),
    }
