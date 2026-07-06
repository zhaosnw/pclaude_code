"""
Misc helpers shared by hook runners.

Port of: src/utils/hooks/hookHelpers.ts

Handles:
- Parsing structured JSON responses from hook stdout
- Merging hook decisions with priority semantics
- Resolving hook-specific output fields (decision, updatedInput, additionalContext, etc.)
"""

from __future__ import annotations

import json
from typing import Any


def normalize_hook_json_output(raw: str) -> dict[str, Any] | None:
    """Parse first JSON object line from hook stdout.

    TS: parseHookJsonOutput — extracts the first complete JSON object
    from stdout, which represents the hook's structured control response.

    The JSON response protocol includes (TS hook response schema):
    - decision: "approve" | "block" — global decision
    - reason: str — reason for blocking (when decision="block")
    - additionalContext: str — context to inject into conversation
    - continue: bool — whether to continue generating
    - stopReason: str — reason for stopping
    - hookSpecificOutput: dict with hookEventName and event-specific fields:
      - PreToolUse: permissionDecision, updatedInput
      - UserPromptSubmit: additionalContext
    """
    for line in raw.splitlines():
        t = line.strip()
        if t.startswith("{"):
            try:
                return json.loads(t)
            except json.JSONDecodeError:
                continue
    return None


def resolve_hook_decision(parsed: dict[str, Any]) -> dict[str, Any]:
    """Resolve the effective hook decision from parsed JSON.

    TS: resolution logic in hook output processing.
    Returns dict with resolved boolean flags and extracted context.
    """
    result: dict[str, Any] = {}

    # Top-level decision: approve or block (TS: "decision" field)
    decision = parsed.get("decision", "")
    if decision == "block":
        result["blocked"] = True
        result["blockReason"] = parsed.get("reason", "Hook blocked this operation")
    elif decision == "approve":
        result["approved"] = True

    # Continue flag
    if "continue" in parsed and parsed.get("continue") is False:
        result["preventContinuation"] = True
        result["stopReason"] = parsed.get(
            "stopReason", parsed.get("reason", "Hook prevented continuation")
        )

    # Additional context to inject
    additional_context = parsed.get("additionalContext", "")
    if additional_context and isinstance(additional_context, str):
        result["additionalContext"] = additional_context

    # Hook-specific output (TS: hookSpecificOutput with hookEventName)
    hook_specific = parsed.get("hookSpecificOutput")
    if isinstance(hook_specific, dict):
        event_name = hook_specific.get("hookEventName", "")
        result["hookSpecificOutput"] = hook_specific
        result["hookEventName"] = event_name

        # PreToolUse specific: permissionDecision, updatedInput
        if event_name == "PreToolUse":
            perm = hook_specific.get("permissionDecision")
            if perm:
                result["permissionDecision"] = perm
                result["permissionDecisionReason"] = hook_specific.get(
                    "permissionDecisionReason", ""
                )
            updated_input = hook_specific.get("updatedInput")
            if isinstance(updated_input, dict):
                result["updatedInput"] = updated_input

        # UserPromptSubmit specific: additionalContext
        if event_name == "UserPromptSubmit":
            ctx = hook_specific.get("additionalContext")
            if ctx and isinstance(ctx, str):
                result["additionalContext"] = ctx

    # Message for display
    if "message" in parsed:
        from hare.utils.messages import create_system_message

        result["message"] = create_system_message(str(parsed["message"]), "info")

    # Blocking error (legacy format: {"blocking": true, "error": "..."})
    if "blocking" in parsed and parsed.get("blocking"):
        error_msg = parsed.get("error", parsed.get("reason", "Blocking hook error"))
        result["blocked"] = True
        result["blockReason"] = error_msg

    return result


def merge_hook_decisions(
    base: dict[str, Any],
    override: dict[str, Any],
) -> dict[str, Any]:
    """Merge two hook decision dicts with priority semantics.

    Later decisions (override) take precedence. Blocked state propagates.
    """
    out = dict(base)
    out.update(override)
    return out


# ---------------------------------------------------------------------------
# Exit code semantics (matching TS hook execution)
# ---------------------------------------------------------------------------


def interpret_hook_exit_code(
    exit_code: int,
    parsed_json: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Interpret hook exit code + JSON response per TS semantics.

    TS exit code semantics table:
    | Exit | JSON decision | Result |
    | 0    | approve/none  | pass   |
    | 0    | block         | block (JSON priority) |
    | 2    | any           | block, stderr shown to model |
    | other| approve       | warn but continue |
    | other| block         | block |

    Returns dict with 'action' ('pass', 'block', 'warn') and 'reason'.
    """
    result: dict[str, Any] = {"action": "pass", "reason": ""}

    if exit_code == 0:
        if parsed_json:
            decision = parsed_json.get("decision", "")
            if decision == "block":
                result["action"] = "block"
                result["reason"] = parsed_json.get(
                    "reason", "Hook blocked (JSON decision)"
                )
        return result

    if exit_code == 2:
        result["action"] = "block"
        result["reason"] = "Hook blocked (exit code 2)"
        return result

    # Other non-zero exit codes
    if parsed_json:
        decision = parsed_json.get("decision", "")
        if decision == "block":
            result["action"] = "block"
            result["reason"] = parsed_json.get("reason", "Hook blocked")
        else:
            result["action"] = "warn"
            result["reason"] = f"Hook exited with code {exit_code}"
    else:
        result["action"] = "warn"
        result["reason"] = f"Hook exited with code {exit_code}"

    return result
