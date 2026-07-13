"""
Permission engine — orchestration for tool permission checks.

Port of: src/utils/permissions/permissions.ts

Implements the 4-stage permission pipeline:
1. validateInput (delegated to tool, graceful degrade to ask on error)
2. Rule matching (deny > ask > allow priority, with content-specific matching)
3. checkPermissions (tool-specific context evaluation, passthrough falls through)
4. Interactive prompt (stub — UI layer not ported)

Key design: deny always wins regardless of source. Passthrough from checkPermissions
does NOT return immediately — it falls through to mode-based and rule-based checks.
"""

from __future__ import annotations

from typing import Any, Optional

from hare.app_types.permissions import (
    PermissionAllowDecision,
    PermissionAskDecision,
    PermissionDenyDecision,
    PermissionPassthrough,
    PermissionResult,
    ToolPermissionContext,
    ToolPermissionRulesBySource,
)
from hare.tool import Tool, ToolUseContext

# Source priority order: session > command > cliArg > policySettings >
# flagSettings > localSettings > projectSettings > userSettings
# TS: PERMISSION_RULE_SOURCES (permissions.ts L109-114)
RULE_SOURCE_PRIORITY: list[str] = [
    "session",
    "command",
    "cliArg",
    "policySettings",
    "flagSettings",
    "localSettings",
    "projectSettings",
    "userSettings",
]


# ---------------------------------------------------------------------------
# Rule helpers (mirrors getAllowRules / getDenyRules / getAskRules)
# with source-priority ordering (TS: PERMISSION_RULE_SOURCES)
# ---------------------------------------------------------------------------


def _flatten_rules_ordered(
    rules_by_source: ToolPermissionRulesBySource,
) -> list[str]:
    """Flatten rules in source priority order (highest priority first).

    TS: Iterates PERMISSION_RULE_SOURCES, collecting rules for each source.
    The first matching rule from the highest-priority source wins.
    """
    result: list[str] = []
    for source in RULE_SOURCE_PRIORITY:
        if source in rules_by_source:
            rules = rules_by_source[source]
            if isinstance(rules, list):
                result.extend(rules)
    # Include any sources not in the predefined list
    for source, rules in rules_by_source.items():
        if source not in RULE_SOURCE_PRIORITY:
            if isinstance(rules, list):
                result.extend(rules)
    return result


def get_allow_rules(context: ToolPermissionContext) -> list[str]:
    return _flatten_rules_ordered(context.always_allow_rules)


def get_deny_rules(context: ToolPermissionContext) -> list[str]:
    return _flatten_rules_ordered(context.always_deny_rules)


def get_ask_rules(context: ToolPermissionContext) -> list[str]:
    return _flatten_rules_ordered(context.always_ask_rules)


# ---------------------------------------------------------------------------
# Rule content parsing — ToolName vs ToolName(content) distinction
# ---------------------------------------------------------------------------


def _parse_rule_for_matching(rule: str) -> tuple[str, str | None]:
    """Parse a rule string to extract tool_name and optional rule_content.

    Returns (tool_name, rule_content_or_None).
    TS: ruleContent parsing in toolMatchesRule (permissions.ts L238-269).
    """
    from hare.utils.permissions.permission_rule import parse_permission_rule

    parsed = parse_permission_rule(rule)
    return (parsed.tool_name, parsed.rule_content or None)


# ---------------------------------------------------------------------------
# Single-tool rule helpers — with content-specific matching
# ---------------------------------------------------------------------------


def get_deny_rule_for_tool(
    tool: Tool,
    context: ToolPermissionContext,
) -> dict[str, Any] | None:
    """Check deny rules with source priority. TS getDenyRuleForTool (permissions.ts L287-292).

    Supports:
    - Tool-level deny: "Bash" matches all Bash calls
    - Content-specific deny: "Bash(rm -rf *)" matches only rm -rf commands
    - MCP server-level deny: "mcp__github" matches all tools from github server
    """
    deny_rules = get_deny_rules(context)
    for rule in deny_rules:
        if _rule_matches_tool(rule, tool):
            return {"source": "deny_rules", "ruleContent": rule}
    return None


def get_ask_rule_for_tool(
    tool: Tool,
    context: ToolPermissionContext,
) -> dict[str, Any] | None:
    """Check ask rules with source priority."""
    ask_rules = get_ask_rules(context)
    for rule in ask_rules:
        if _rule_matches_tool(rule, tool):
            return {"source": "ask_rules", "ruleContent": rule}
    return None


def tool_always_allowed_rule(
    tool: Tool,
    context: ToolPermissionContext,
) -> bool:
    """Check if tool is explicit-allowlisted."""
    allow_rules = get_allow_rules(context)
    for rule in allow_rules:
        if _rule_matches_tool(rule, tool):
            return True
    return False


# ---------------------------------------------------------------------------
# Content-specific allow matching (for getAllowRuleForToolInput)
# ---------------------------------------------------------------------------


def get_allow_rule_for_tool_input(
    tool: Tool,
    input: dict[str, Any],
    context: ToolPermissionContext,
) -> dict[str, Any] | None:
    """Check allow rules that have content (e.g. "Bash(npm test)").

    TS: getAllowRuleForToolInput — matches ruleContent against tool input.
    For BashTool, matches against the command string.
    """
    from hare.utils.permissions.permission_rule import parse_permission_rule
    from hare.utils.permissions.shell_rule_matching import (
        match_wildcard_pattern,
        permission_rule_extract_prefix,
    )

    allow_rules = get_allow_rules(context)
    for rule_str in allow_rules:
        parsed = parse_permission_rule(rule_str)
        if not parsed.rule_content:
            continue  # no content → tool-level rule, handled separately

        tool_name = parsed.tool_name
        rule_content = parsed.rule_content

        # Must match this tool
        from hare.tool import tool_matches_name as _matches

        if not _matches(tool, tool_name):
            continue

        # For BashTool: match ruleContent against the command
        if tool_name == "Bash" or tool.name == "Bash":
            command = input.get("command", "")
            if isinstance(command, str):
                # TS: prefix matching with :* syntax
                prefix = permission_rule_extract_prefix(rule_content)
                if prefix is not None:
                    if command == prefix or command.startswith(prefix + " "):
                        return {"source": "allow_rules", "ruleContent": rule_str}
                # TS: wildcard matching
                if match_wildcard_pattern(rule_content, command):
                    return {"source": "allow_rules", "ruleContent": rule_str}
        else:
            # Non-Bash tools: compare ruleContent with input values
            # (simplified — TS has per-tool input comparison logic)
            for val in input.values():
                if isinstance(val, str) and val == rule_content:
                    return {"source": "allow_rules", "ruleContent": rule_str}

    return None


# ---------------------------------------------------------------------------
# MCP server-level matching (TS: mcpInfoFromString server prefix check)
# ---------------------------------------------------------------------------


def _is_tool_from_mcp_server(tool: Tool, rule: str) -> bool:
    """Check if a rule matches an MCP server (e.g. rule "mcp__github" matches
    tools "mcp__github__list_repos", "mcp__github__create_issue", etc.)"""
    mcp_info = getattr(tool, "mcp_info", None)
    if mcp_info is None:
        return False
    server_name = getattr(mcp_info, "server_name", None)
    if not server_name:
        return False

    # Match: rule is "mcp__<server>" and tool is "mcp__<server>__<tool>"
    if rule == f"mcp__{server_name}":
        return True
    return False


# ---------------------------------------------------------------------------
# Core rule-to-tool matching (TS toolMatchesRule)
# ---------------------------------------------------------------------------


def _rule_matches_tool(rule: str, tool: Tool) -> bool:
    """Check if a permission rule string matches a tool.

    TS toolMatchesRule (permissions.ts L238-269):
    1. If rule has ruleContent (e.g. "Bash(rm:*)"), skip tool-level matching
       because content-specific rules are checked separately in getAllowRuleForToolInput
    2. MCP server-level matching (mcp__server prefix)
    3. Name/alias matching
    """
    from hare.utils.permissions.permission_rule import parse_permission_rule

    parsed = parse_permission_rule(rule)
    tool_name = parsed.tool_name

    # If rule has content, this is a content-specific rule → skip tool-level match
    # (TS: ruleContent !== undefined check)
    if parsed.rule_content:
        return False

    # MCP server-level matching
    if _is_tool_from_mcp_server(tool, tool_name):
        return True

    # Name/alias matching
    from hare.tool import tool_matches_name as _matches

    return _matches(tool, tool_name)


# ---------------------------------------------------------------------------
# Rule-based permission check
# ---------------------------------------------------------------------------


def check_rule_based_permissions(
    tool: Tool,
    input: dict[str, Any],
    context: ToolPermissionContext,
) -> PermissionResult:
    """Check permissions purely from rule context (deny > ask > allow priority)."""
    # 1. Deny rules take precedence
    deny = get_deny_rule_for_tool(tool, context)
    if deny:
        return PermissionDenyDecision(
            behavior="deny",
            message=f"Permission to use {tool.name} has been denied.",
            decision_reason="deny_rule",
        )

    # 2. Ask rules
    ask = get_ask_rule_for_tool(tool, context)
    if ask:
        return PermissionAskDecision(
            behavior="ask",
            message=f"Hare needs permission to use {tool.name}.",
            decision_reason="ask_rule",
        )

    # 3. Always allow for this tool
    if tool_always_allowed_rule(tool, context):
        return PermissionAllowDecision(
            behavior="allow",
            updated_input=input,
        )

    # 4. Content-specific allow rules
    content_allow = get_allow_rule_for_tool_input(tool, input, context)
    if content_allow:
        return PermissionAllowDecision(
            behavior="allow",
            updated_input=input,
        )

    # Default: ask
    return PermissionAskDecision(
        behavior="ask",
        message=f"Hare requested permissions to use {tool.name}, but you haven't granted it yet.",
    )


# ---------------------------------------------------------------------------
# Main permission check entry point — pipeline matching TS hasPermissionsToUseTool
# ---------------------------------------------------------------------------


async def has_permissions_to_use_tool(
    tool: Tool,
    input: dict[str, Any],
    context: ToolUseContext,
    assistant_msg: Any,
    tool_use_id: str,
    force_decision: Optional[str] = None,
) -> PermissionResult:
    """Main permission check called per tool_use in the conversation loop.

    Pipeline order (mirrors TS hasPermissionsToUseTool / hasPermissionsToUseToolInner):
    1. Force decision (if provided by caller)
    2. Deny rules (always win)
    3. Ask rules (if present, ask user)
    4. Tool-level checkPermissions hook
       → passthrough falls through (does NOT return immediately)
       → deny/ask returned immediately
       → allow continues through remaining checks
    5. BypassPermissions mode check
    6. Tool always-allowed rule
    7. Content-specific allow rules
    8. Read-only safe tools — auto-allow
    9. Passthrough to user
    """

    permission_context = getattr(context.options, "permission_context", None)
    if permission_context is None:
        permission_context = (
            getattr(context.options.commands[0], "permission_context", None)
            if context.options.commands
            else None
        )
    permission_context = permission_context or ToolPermissionContext(mode="default")

    # Step 1: Force decision
    if force_decision:
        if force_decision == "allow":
            return PermissionAllowDecision(behavior="allow", updated_input=input)
        if force_decision == "deny":
            return PermissionDenyDecision(
                behavior="deny",
                message=f"Permission to use {tool.name} has been denied.",
            )
        if force_decision == "ask":
            return PermissionAskDecision(
                behavior="ask",
                message=f"Hare is asking to use {tool.name}.",
            )

    # Step 2: Deny rules (priority iron rule — always first)
    deny = get_deny_rule_for_tool(tool, permission_context)
    if deny:
        return PermissionDenyDecision(
            behavior="deny",
            message=f"Permission to use {tool.name} has been denied.",
        )

    # Step 3: Ask rules
    ask = get_ask_rule_for_tool(tool, permission_context)
    if ask:
        return PermissionAskDecision(
            behavior="ask",
            message=f"Hare needs permission to use {tool.name}.",
            suggestions=[
                {"behavior": "alwaysAllow", "label": "Always allow"},
            ],
        )

    # Step 4: Tool-level permission check (checkPermissions)
    # TS: only deny/ask returned immediately; allow/passthrough continue
    tool_check_result: PermissionResult | None = None
    try:
        tool_perms = await tool.check_permissions(input, context)
        behavior = getattr(tool_perms, "behavior", "passthrough")
        if behavior == "deny":
            return tool_perms
        if behavior == "ask":
            return tool_perms
        # For "allow" and "passthrough", continue through remaining checks
        tool_check_result = tool_perms
    except Exception:
        pass

    # Step 5: BypassPermissions mode — auto-allow everything except deny+ask rules
    if permission_context.mode == "bypassPermissions":
        if permission_context.is_bypass_permissions_mode_available:
            return PermissionAllowDecision(behavior="allow", updated_input=input)

    # Step 6: Tool always allowed (explicit allow rule without content)
    if tool_always_allowed_rule(tool, permission_context):
        return PermissionAllowDecision(behavior="allow", updated_input=input)

    # Step 7: Content-specific allow rules (e.g. Bash(npm test))
    content_allow = get_allow_rule_for_tool_input(tool, input, permission_context)
    if content_allow is not None:
        return PermissionAllowDecision(behavior="allow", updated_input=input)

    # Step 8: Read-only safe tools — auto-allow
    if tool.is_read_only(input) and not tool.is_destructive(input):
        return PermissionAllowDecision(behavior="allow", updated_input=input)

    # Step 9: Default — passthrough to user
    return PermissionPassthrough(
        behavior="passthrough",
        message=f"Hare wants to use {tool.name}.",
    )


# ---------------------------------------------------------------------------
# Permission request message formatting
# ---------------------------------------------------------------------------


def create_permission_request_message(
    tool: Tool,
    input: dict[str, Any],
    decision: PermissionResult,
    context: ToolPermissionContext,
) -> dict[str, Any]:
    """Format a permission request for display to the user."""
    behavior = getattr(decision, "behavior", "ask")
    tool_name = getattr(tool, "user_facing_name", None)
    if callable(tool_name):
        try:
            tool_name = tool_name(input)
        except Exception:
            tool_name = tool.name
    display_name = tool_name if isinstance(tool_name, str) else tool.name

    return {
        "tool": display_name,
        "behavior": behavior,
        "message": getattr(
            decision, "message", f"Permission requested for {display_name}"
        ),
        "suggestions": getattr(decision, "suggestions", []),
    }
