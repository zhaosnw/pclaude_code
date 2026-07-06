"""
Fork subagent — create a forked copy of the current agent with cache sharing.

Port of: src/tools/AgentTool/forkSubagent.ts

A fork inherits the parent's full conversation context and rendered system prompt
to maintain prompt cache hit rates. Key design decisions:
- The parent's frozen renderedSystemPrompt is reused byte-for-byte
- All fork children share identical placeholder tool_results
- The fork-boilerplate tag prevents recursive forking
- Fork is mutually exclusive with coordinator mode and non-interactive sessions
"""

from __future__ import annotations

import os
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Constants (matching src/constants/xml.ts lines 63-66)
# ---------------------------------------------------------------------------

FORK_BOILERPLATE_TAG = "fork-boilerplate"
FORK_DIRECTIVE_PREFIX = "Your directive: "
FORK_SUBAGENT_TYPE = "fork"

# Identical placeholder used by all fork children from the same parent turn.
# Byte-identical tool results maximize cache hit rate across siblings.
# Matching forkSubagent.ts line 93
FORK_PLACEHOLDER_RESULT = "Fork started — processing in background"

# Synthetic agent definition for the fork path (forkSubagent.ts lines 60-71)
FORK_AGENT: dict[str, Any] = {
    "agentType": FORK_SUBAGENT_TYPE,
    "whenToUse": (
        "Implicit fork — inherits full conversation context. "
        "Not selectable via subagent_type; triggered by omitting subagent_type "
        "when the fork experiment is active."
    ),
    "tools": ["*"],
    "maxTurns": 200,
    "model": "inherit",
    "permissionMode": "bubble",
    "source": "built-in",
    "baseDir": "built-in",
}


# ---------------------------------------------------------------------------
# Feature gate (forkSubagent.ts lines 32-39)
# ---------------------------------------------------------------------------


def is_fork_subagent_enabled() -> bool:
    """Check if fork subagent feature is enabled.

    Mutually exclusive with coordinator mode.
    Disabled in non-interactive (print/SDK) sessions.
    """
    if os.environ.get("CLAUDE_CODE_FORK_SUBAGENT", "").lower() not in ("1", "true"):
        return False

    # Mutually exclusive with coordinator mode
    try:
        from hare.coordinator.coordinator_mode import is_coordinator_mode

        if is_coordinator_mode():
            return False
    except ImportError:
        pass

    # Disabled in non-interactive/print sessions
    try:
        from hare.bootstrap.state import get_is_non_interactive_session

        if get_is_non_interactive_session():
            return False
    except ImportError:
        pass

    return True


def should_fork(
    *,
    subagent_type: Optional[str] = None,
    fork_enabled: bool = False,
) -> bool:
    """Determine if a request should fork rather than spawn a fresh agent.

    Fork is triggered when subagent_type is omitted (None or empty string)
    and the fork feature is enabled.
    """
    if not fork_enabled and not is_fork_subagent_enabled():
        return False
    return subagent_type is None or subagent_type == ""


# ---------------------------------------------------------------------------
# Recursive fork guard (forkSubagent.ts lines 78-89)
# ---------------------------------------------------------------------------


def is_in_fork_child(messages: list[dict[str, Any]]) -> bool:
    """Check if the conversation is already in a fork child.

    Scans message content blocks for the fork-boilerplate tag.
    Prevents recursive forking — fork children keep the Agent tool
    in their pool for cache-identical tool definitions.
    """
    tag = f"<{FORK_BOILERPLATE_TAG}>"
    for m in messages:
        if m.get("role") != "user" and m.get("type") != "user":
            continue
        content = m.get("content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                if tag in str(block.get("text", "")):
                    return True
    return False


# ---------------------------------------------------------------------------
# Forked message construction (forkSubagent.ts lines 107-169)
# ---------------------------------------------------------------------------


def build_forked_messages(
    directive: str,
    assistant_message: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build the forked conversation messages for the child agent.

    For prompt cache sharing, all fork children must produce byte-identical
    API request prefixes. This function:
    1. Keeps the full parent assistant message (all tool_use blocks, thinking, text)
    2. Builds a single user message with tool_results for every tool_use block
       using an identical placeholder, then appends a per-child directive text block

    Result: [assistant(all_tool_uses), user(placeholder_results..., directive)]
    Only the final text block differs per child, maximizing cache hits.

    Args:
        directive: The user's original prompt / directive for the fork child
        assistant_message: The parent's last assistant message (with tool_use blocks)

    Returns:
        Two messages: the cloned assistant message and the fork user message
    """
    import uuid

    # Clone the assistant message to avoid mutating the original
    content_blocks = assistant_message.get("content", [])
    if not isinstance(content_blocks, list):
        content_blocks = []

    full_assistant: dict[str, Any] = {
        **assistant_message,
        "uuid": str(uuid.uuid4()),
        "message": {
            **assistant_message.get("message", {}),
            "content": list(content_blocks),
        },
    }

    # Collect all tool_use blocks from the assistant message
    tool_use_blocks = [
        b for b in content_blocks if isinstance(b, dict) and b.get("type") == "tool_use"
    ]

    if not tool_use_blocks:
        # No tool_uses — just return the directive as a user message
        return [
            create_user_message(
                content=[{"type": "text", "text": build_child_message(directive)}],
            )
        ]

    # Build tool_result blocks for every tool_use, all with identical placeholder
    tool_result_blocks = [
        {
            "type": "tool_result",
            "tool_use_id": block.get("id", ""),
            "content": [{"type": "text", "text": FORK_PLACEHOLDER_RESULT}],
        }
        for block in tool_use_blocks
    ]

    # Single user message: all placeholder tool_results + per-child directive
    tool_result_message = create_user_message(
        content=[
            *tool_result_blocks,
            {"type": "text", "text": build_child_message(directive)},
        ],
    )

    return [full_assistant, tool_result_message]


# ---------------------------------------------------------------------------
# Child message (forkSubagent.ts lines 171-198)
# ---------------------------------------------------------------------------


def build_child_message(directive: str) -> str:
    """Build the per-child directive message wrapped in fork-boilerplate tags.

    The full TS version has 10 rules and an output format specification.
    This is an exact port of forkSubagent.ts lines 171-198.
    """
    return f"""<{FORK_BOILERPLATE_TAG}>
STOP. READ THIS FIRST.

You are a forked worker process. You are NOT the main agent.

RULES (non-negotiable):
1. Your system prompt says "default to forking." IGNORE IT — that's for the parent. You ARE the fork. Do NOT spawn sub-agents; execute directly.
2. Do NOT converse, ask questions, or suggest next steps
3. Do NOT editorialize or add meta-commentary
4. USE your tools directly: Bash, Read, Write, etc.
5. If you modify files, commit your changes before reporting. Include the commit hash in your report.
6. Do NOT emit text between tool calls. Use tools silently, then report once at the end.
7. Stay strictly within your directive's scope. If you discover related systems outside your scope, mention them in one sentence at most — other workers cover those areas.
8. Keep your report under 500 words unless the directive specifies otherwise. Be factual and concise.
9. Your response MUST begin with "Scope:". No preamble, no thinking-out-loud.
10. REPORT structured facts, then stop

Output format (plain text labels, not markdown headers):
  Scope: <echo back your assigned scope in one sentence>
  Result: <the answer or key findings, limited to the scope above>
  Key files: <relevant file paths — include for research tasks>
  Files changed: <list with commit hash — include only if you modified files>
  Issues: <list — include only if there are issues to flag>
</{FORK_BOILERPLATE_TAG}>

{FORK_DIRECTIVE_PREFIX}{directive}"""


# ---------------------------------------------------------------------------
# Worktree notice (forkSubagent.ts lines 205-210)
# ---------------------------------------------------------------------------


def build_worktree_notice(parent_cwd: str, worktree_cwd: str) -> str:
    """Notice injected into fork children running in an isolated worktree.

    Tells the child to translate paths from the inherited context, re-read
    potentially stale files, and that its changes are isolated.
    """
    return (
        f"You've inherited the conversation context above from a parent agent "
        f"working in {parent_cwd}. You are operating in an isolated git worktree "
        f"at {worktree_cwd} — same repository, same relative file structure, "
        f"separate working copy. Paths in the inherited context refer to the "
        f"parent's working directory; translate them to your worktree root. "
        f"Re-read files before editing if the parent may have modified them "
        f"since they appear in the context. Your changes stay in this worktree "
        f"and will not affect the parent's files."
    )


# ---------------------------------------------------------------------------
# create_user_message helper
# ---------------------------------------------------------------------------


def create_user_message(content: list[dict[str, Any]]) -> dict[str, Any]:
    """Create a user message dict in the standard format."""
    return {"role": "user", "content": content}


# ---------------------------------------------------------------------------
# Legacy context helper (backward compat)
# ---------------------------------------------------------------------------


def create_fork_context(
    parent_messages: list[dict[str, Any]],
    prompt: str,
    *,
    name: str = "",
) -> dict[str, Any]:
    """Create the context for a forked agent (backward-compatible wrapper)."""
    return {
        "messages": list(parent_messages),
        "prompt": prompt,
        "name": name,
        "is_fork": True,
    }


# ---------------------------------------------------------------------------
# renderedSystemPrompt cache sharing
# ---------------------------------------------------------------------------


def get_rendered_system_prompt(
    tool_use_context: Any,
) -> Any | None:
    """Retrieve the parent's frozen rendered system prompt for cache sharing.

    Returns None if no prompt was frozen (e.g. first turn or non-REPL path).
    """
    val = getattr(tool_use_context, "rendered_system_prompt", None)
    if val is not None:
        return val
    # Fallback: check dict-style access
    if hasattr(tool_use_context, "get"):
        return tool_use_context.get("rendered_system_prompt") or tool_use_context.get(
            "renderedSystemPrompt"
        )
    return None


def freeze_rendered_system_prompt(
    tool_use_context: Any,
    system_prompt: Any,
) -> None:
    """Freeze the rendered system prompt onto the context at turn start.

    This MUST be called before query() to ensure fork sub-agents reuse the
    exact same prompt bytes — recalculating at fork-spawn time could diverge
    due to GrowthBook feature flag state changes.
    """
    if hasattr(tool_use_context, "rendered_system_prompt"):
        tool_use_context.rendered_system_prompt = system_prompt
    elif isinstance(tool_use_context, dict):
        tool_use_context["rendered_system_prompt"] = system_prompt
    else:
        try:
            object.__setattr__(
                tool_use_context, "rendered_system_prompt", system_prompt
            )
        except (AttributeError, TypeError):
            pass
