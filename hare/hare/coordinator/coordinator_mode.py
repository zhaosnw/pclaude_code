"""
Coordinator mode — multi-agent orchestration with coordinator-worker architecture.

Port of: src/coordinator/coordinatorMode.ts

Key design (matching TS):
- Dual gating: compile-time feature gate + CLAUDE_CODE_COORDINATOR_MODE env var
- Mutually exclusive with Fork mode (is_coordinator_mode disables fork)
- Coordinator has 4 core tools; workers get standard tools minus internal set
- Simple mode: Bash/Read/Edit only; Full mode: all standard tools
- Scratchpad: shared temp directory for cross-worker knowledge
- matchSessionMode: auto-flip env var to match session mode on resume
"""

from __future__ import annotations

import os
from typing import Any


# ---------------------------------------------------------------------------
# Internal worker tools — tools that workers should NOT see (TS L28-33)
# ---------------------------------------------------------------------------

_INTERNAL_WORKER_TOOLS = frozenset(
    [
        "TeamCreate",
        "TeamDelete",
        "SendMessage",
        "SyntheticOutput",
    ]
)


def get_internal_worker_tools() -> frozenset[str]:
    """Get the set of tools reserved for the Coordinator only."""
    return _INTERNAL_WORKER_TOOLS


# ---------------------------------------------------------------------------
# Feature gate + env var (TS L36-41)
# ---------------------------------------------------------------------------


def is_coordinator_mode() -> bool:
    """Check if Coordinator mode is active.

    TS isCoordinatorMode:
    - Requires compile-time feature gate (always True in hare)
    - CLAUDE_CODE_COORDINATOR_MODE env var controls runtime activation
    Mutually exclusive with Fork mode.
    """
    val = os.environ.get("CLAUDE_CODE_COORDINATOR_MODE", "")
    return val.lower() in ("1", "true", "yes")


# ---------------------------------------------------------------------------
# Session mode matching (TS L48-82)
# ---------------------------------------------------------------------------


def match_session_mode(
    session_mode: str | None,
) -> str | None:
    """Match current mode to session's stored mode on resume.

    TS matchSessionMode: if current mode != session mode, auto-flip env var
    so that is_coordinator_mode() returns the correct value for the resumed
    session. Returns warning/notification string or None.
    """
    if not session_mode:
        return None

    current_is_coordinator = is_coordinator_mode()
    session_is_coordinator = session_mode == "coordinator"

    if current_is_coordinator == session_is_coordinator:
        return None

    if session_is_coordinator:
        os.environ["CLAUDE_CODE_COORDINATOR_MODE"] = "1"
    else:
        os.environ.pop("CLAUDE_CODE_COORDINATOR_MODE", None)

    from hare.services.analytics import log_event

    log_event("tengu_coordinator_mode_switched", {"to": session_mode})

    return (
        "Entered coordinator mode to match resumed session."
        if session_is_coordinator
        else "Exited coordinator mode to match resumed session."
    )


# ---------------------------------------------------------------------------
# User context: worker tool descriptions (TS L84-108)
# ---------------------------------------------------------------------------


def _is_scratchpad_enabled() -> bool:
    """Check if scratchpad feature gate is enabled.

    TS: checkStatsigFeatureGate_CACHED_MAY_BE_STALE('tengu_scratch')
    """
    from hare.utils.env_utils import is_env_truthy

    return is_env_truthy(os.environ.get("HARE_SCRATCHPAD_ENABLED"))


def get_coordinator_user_context(
    mcp_servers: list[dict[str, Any]] | None = None,
    scratchpad_dir: str | None = None,
) -> dict[str, str]:
    """Get user context for Coordinator describing worker capabilities.

    TS getCoordinatorUserContext:
    - Simple mode: workers have Bash, Read, Edit
    - Full mode: all ASYNC_AGENT_ALLOWED_TOOLS minus INTERNAL_WORKER_TOOLS
    - MCP tool info and scratchpad location appended as appropriate
    """
    if not is_coordinator_mode():
        return {}

    from hare.utils.env_utils import is_env_truthy

    if is_env_truthy(os.environ.get("CLAUDE_CODE_SIMPLE")):
        worker_tools = "Bash, Read, Edit"
    else:
        from hare.tools import ASYNC_AGENT_ALLOWED_TOOLS

        internal = _INTERNAL_WORKER_TOOLS
        worker_tools = ", ".join(
            sorted(name for name in ASYNC_AGENT_ALLOWED_TOOLS if name not in internal)
        )

    content = (
        f"Workers spawned via the Agent tool have access to these tools: {worker_tools}"
    )

    if mcp_servers:
        server_names = ", ".join(s.get("name", "unknown") for s in mcp_servers)
        content += (
            f"\n\nWorkers also have access to MCP tools from connected "
            f"MCP servers: {server_names}"
        )

    if scratchpad_dir and _is_scratchpad_enabled():
        content += (
            f"\n\nScratchpad directory: {scratchpad_dir}\n"
            f"Workers can read and write here without permission prompts. "
            f"Use this for durable cross-worker knowledge — structure files "
            f"however fits the work."
        )

    return {"workerToolsContext": content}


# ---------------------------------------------------------------------------
# Coordinator system prompt (TS getCoordinatorSystemPrompt L113+)
# ---------------------------------------------------------------------------


def get_coordinator_system_prompt() -> str:
    """Get the Coordinator system prompt defining the orchestrator role.

    TS getCoordinatorSystemPrompt — defines full behavior specification:
    - Coordinator role: orchestrate, not execute
    - Tool set: Agent, SendMessage, TaskStop, subscribe_pr_activity
    - Task notification format: XML <task-notification>
    - Workflow phases: Research → Synthesis → Implementation → Verification
    - Concurrency strategy: read-only tasks parallel, write tasks serial
    """
    from hare.utils.env_utils import is_env_truthy

    worker_capabilities = (
        "Workers have access to Bash, Read, and Edit tools, plus MCP tools "
        "from configured MCP servers."
        if is_env_truthy(os.environ.get("CLAUDE_CODE_SIMPLE"))
        else "Workers have access to standard tools, MCP tools from configured "
        "MCP servers, and project skills via the Skill tool. Delegate skill "
        "invocations (e.g. /commit, /verify) to workers."
    )

    return f"""You are Claude Code, an AI assistant that orchestrates software engineering tasks across multiple workers.

## 1. Your Role

You are a **coordinator**. Your job is to:
- Help the user achieve their goal
- Direct workers to research, implement and verify code changes
- Synthesize results and communicate with the user
- Answer questions directly when possible — don't delegate work that you can handle without tools

Every message you send is to the user. Worker results and system notifications are internal signals, not conversation partners — never thank or acknowledge them. Summarize new information for the user as it arrives.

## 2. Your Tools

- **Agent** - Spawn a new worker
- **SendMessage** - Continue an existing worker
- **TaskStop** - Stop a running worker

When calling Agent:
- Do not set the model parameter. Workers need the default model.
- Do not use one worker to check on another. Workers will notify you when they are done.
- After launching agents, briefly tell the user what you launched and end your response.
- Never fabricate or predict agent results — results arrive as separate messages.

### Agent Results

Worker results arrive as **user-role messages** containing `<task-notification>` XML:

```xml
<task-notification>
<task-id>{{agentId}}</task-id>
<status>completed|failed|killed</status>
<summary>{{human-readable status summary}}</summary>
<result>{{agent's final text response}}</result>
<usage>
  <total_tokens>N</total_tokens>
  <tool_uses>N</tool_uses>
  <duration_ms>N</duration_ms>
</usage>
</task-notification>
```

## 3. Workflow

### Phase 1: Research
Launch parallel read-only workers to investigate different aspects.
Write findings to the scratchpad. Workers run concurrently.

### Phase 2: Synthesis
Read ALL worker findings yourself. Understand the full picture.
Write an implementation spec with specific file paths and change descriptions.

### Phase 3: Implementation
Assign workers to implement changes. One worker per file set to avoid conflicts.
Workers can use Edit, Write, and Bash tools.

### Phase 4: Verification
Assign verification workers to test correctness. Can run in parallel.

## 4. Key Rules

1. **Never write "based on your findings"** — read and understand findings yourself before writing specs.
2. **Fan out** — launch multiple workers in one message for parallelism.
3. **Research is parallel-safe** — launch all research workers together.
4. **Implementation is file-set focused** — don't assign overlapping file sets.
5. **Use the scratchpad** — workers discover, you synthesize, specs in scratchpad.

{worker_capabilities}"""


# ---------------------------------------------------------------------------
# Tool pool filtering (TS: Coordinator uses COORDINATOR_MODE_ALLOWED_TOOLS)
# ---------------------------------------------------------------------------


def apply_coordinator_tool_filter(tools: list[Any]) -> list[Any]:
    """Filter tool pool for Coordinator mode.

    TS: Coordinator only has Agent, SendMessage, TaskStop + PR subscription tools.
    This is a simplified version matching the import in tool_pool.py.
    """
    from hare.tools import COORDINATOR_MODE_ALLOWED_TOOLS

    if not COORDINATOR_MODE_ALLOWED_TOOLS:
        return list(tools)

    allowed = frozenset(COORDINATOR_MODE_ALLOWED_TOOLS)
    return [t for t in tools if getattr(t, "name", "") in allowed]
