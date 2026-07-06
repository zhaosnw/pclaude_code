"""Detect shadowed allow/deny rules. Port of shadowedRuleDetection.ts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from hare.app_types.permissions import (
    PermissionRule,
    PermissionRuleSource,
    ToolPermissionContext,
)

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

ShadowType = Literal["ask", "deny"]


@dataclass
class UnreachableRule:
    """Represents an unreachable permission rule with explanation."""

    rule: PermissionRule
    reason: str
    shadowed_by: PermissionRule
    shadow_type: ShadowType
    fix: str


@dataclass
class DetectUnreachableRulesOptions:
    """Options for detecting unreachable rules.

    Attributes:
        sandbox_auto_allow_enabled: Whether sandbox auto-allow is enabled for
            Bash commands. When true, tool-wide Bash ask rules from personal
            settings don't block specific Bash allow rules because sandboxed
            commands are auto-allowed.
    """

    sandbox_auto_allow_enabled: bool = False


# ---------------------------------------------------------------------------
# Source display helpers
# ---------------------------------------------------------------------------

_PERMISSION_SOURCE_DISPLAY: dict[PermissionRuleSource, str] = {
    "userSettings": "user settings",
    "projectSettings": "project settings",
    "localSettings": "local settings",
    "flagSettings": "flag settings",
    "policySettings": "policy settings",
    "cliArg": "CLI arguments",
    "command": "command",
    "session": "session",
}


def _format_source(source: PermissionRuleSource) -> str:
    """Convert a PermissionRuleSource to a user-facing display string."""
    return _PERMISSION_SOURCE_DISPLAY.get(source, source)


def _is_shared_setting_source(source: PermissionRuleSource) -> bool:
    """Check if a permission rule source is shared (visible to other users).

    Shared settings include:
    - projectSettings: Committed to git, shared with team
    - policySettings: Enterprise-managed, pushed to all users
    - command: From slash command frontmatter, potentially shared
    """
    return source in ("projectSettings", "policySettings", "command")


_BASH_TOOL_NAME = "Bash"


def _generate_fix_suggestion(
    shadow_type: ShadowType,
    shadowing_rule: PermissionRule,
    shadowed_rule: PermissionRule,
) -> str:
    """Generate a fix suggestion based on the shadow type."""
    shadowing_source = _format_source(shadowing_rule.source)
    shadowed_source = _format_source(shadowed_rule.source)
    tool_name = shadowing_rule.rule_value.tool_name

    if shadow_type == "deny":
        return (
            f'Remove the "{tool_name}" deny rule from {shadowing_source}, '
            f"or remove the specific allow rule from {shadowed_source}"
        )
    return (
        f'Remove the "{tool_name}" ask rule from {shadowing_source}, '
        f"or remove the specific allow rule from {shadowed_source}"
    )


# ---------------------------------------------------------------------------
# Shadowing checks
# ---------------------------------------------------------------------------

_ShadowResult = tuple[
    bool, PermissionRule | None, ShadowType | None
]


def _is_allow_rule_shadowed_by_ask_rule(
    allow_rule: PermissionRule,
    ask_rules: list[PermissionRule],
    sandbox_auto_allow_enabled: bool,
) -> _ShadowResult:
    """Check if a specific allow rule is shadowed (unreachable) by an ask rule.

    An allow rule is unreachable when:
    1. There's a tool-wide ask rule (e.g., "Bash" in ask list)
    2. And a specific allow rule (e.g., "Bash(ls:*)" in allow list)

    Exception: For Bash with sandbox auto-allow enabled, tool-wide ask rules
    from PERSONAL settings don't shadow specific allow rules.
    """
    rule_content = allow_rule.rule_value.rule_content
    tool_name = allow_rule.rule_value.tool_name

    # Only check allow rules that have specific content (e.g., "Bash(ls:*)")
    # Tool-wide allow rules cannot be shadowed by ask rules
    if not rule_content:
        return (False, None, None)

    # Find any tool-wide ask rule for the same tool
    shadowing_ask_rule = next(
        (
            rule
            for rule in ask_rules
            if rule.rule_value.tool_name == tool_name
            and not rule.rule_value.rule_content
        ),
        None,
    )

    if not shadowing_ask_rule:
        return (False, None, None)

    # Special case: Bash with sandbox auto-allow from personal settings
    if tool_name == _BASH_TOOL_NAME and sandbox_auto_allow_enabled:
        if not _is_shared_setting_source(shadowing_ask_rule.source):
            return (False, None, None)
        # Fall through to mark as shadowed - shared settings should always warn

    return (True, shadowing_ask_rule, "ask")


def _is_allow_rule_shadowed_by_deny_rule(
    allow_rule: PermissionRule,
    deny_rules: list[PermissionRule],
) -> _ShadowResult:
    """Check if an allow rule is shadowed (completely blocked) by a deny rule.

    Deny rules are checked first in the permission evaluation order,
    so the allow rule will never be reached - the tool is always denied.
    """
    rule_content = allow_rule.rule_value.rule_content
    tool_name = allow_rule.rule_value.tool_name

    # Only check allow rules that have specific content (e.g., "Bash(ls:*)")
    if not rule_content:
        return (False, None, None)

    # Find any tool-wide deny rule for the same tool
    shadowing_deny_rule = next(
        (
            rule
            for rule in deny_rules
            if rule.rule_value.tool_name == tool_name
            and not rule.rule_value.rule_content
        ),
        None,
    )

    if not shadowing_deny_rule:
        return (False, None, None)

    return (True, shadowing_deny_rule, "deny")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect_unreachable_rules(
    context: ToolPermissionContext,
    options: DetectUnreachableRulesOptions | None = None,
) -> list[UnreachableRule]:
    """Detect all unreachable permission rules in the given context.

    Currently detects:
    - Allow rules shadowed by tool-wide deny rules (completely blocked)
    - Allow rules shadowed by tool-wide ask rules (will always prompt)

    Args:
        context: The tool permission context to analyze.
        options: Configuration options (sandbox auto-allow, etc.)

    Returns:
        List of unreachable rules with explanations and fix suggestions.
    """
    if options is None:
        options = DetectUnreachableRulesOptions()

    unreachable: list[UnreachableRule] = []

    allow_rules = _get_rules(context, "allow")
    ask_rules = _get_rules(context, "ask")
    deny_rules = _get_rules(context, "deny")

    for allow_rule in allow_rules:
        # Check deny shadowing first (more severe)
        shadowed, shadowing_rule, shadow_type = _is_allow_rule_shadowed_by_deny_rule(
            allow_rule, deny_rules
        )
        if not shadowed:
            shadowed, shadowing_rule, shadow_type = (
                _is_allow_rule_shadowed_by_ask_rule(
                    allow_rule, ask_rules, options.sandbox_auto_allow_enabled
                )
            )

        if shadowed:
            assert shadowing_rule is not None
            assert shadow_type is not None
            shadow_source = _format_source(shadowing_rule.source)
            action = "Blocked" if shadow_type == "deny" else "Shadowed"
            reason = (
                f'{action} by "{shadowing_rule.rule_value.tool_name}" '
                f"{shadow_type} rule (from {shadow_source})"
            )
            unreachable.append(
                UnreachableRule(
                    rule=allow_rule,
                    reason=reason,
                    shadowed_by=shadowing_rule,
                    shadow_type=shadow_type,
                    fix=_generate_fix_suggestion(
                        shadow_type, shadowing_rule, allow_rule
                    ),
                )
            )

    return unreachable


def _get_rules(
    context: ToolPermissionContext,
    behavior: Literal["allow", "ask", "deny"],
) -> list[PermissionRule]:
    """Extract PermissionRule objects from a ToolPermissionContext by behavior.

    The context stores rules as strings keyed by (source, behavior). This
    function converts those strings into PermissionRule objects using the
    permission rule parser.
    """
    from hare.utils.permissions.permission_rule import parse_permission_rule

    if behavior == "allow":
        rules_by_source = context.always_allow_rules
    elif behavior == "deny":
        rules_by_source = context.always_deny_rules
    else:
        rules_by_source = context.always_ask_rules

    result: list[PermissionRule] = []
    for source, rule_strings in rules_by_source.items():
        for rule_str in rule_strings:
            parsed = parse_permission_rule(rule_str)
            result.append(
                PermissionRule(
                    source=source,
                    rule_behavior=behavior,
                    rule_value=parsed,
                )
            )
    return result


def find_shadowed_rules(rules: list[dict[str, Any]]) -> list[tuple[int, int]]:
    """Return pairs of indices where an earlier rule shadows a later one."""
    shadows: list[tuple[int, int]] = []
    for i, a in enumerate(rules):
        for j in range(i + 1, len(rules)):
            b = rules[j]
            if a.get("pattern") == b.get("pattern") and a.get("type") != b.get("type"):
                shadows.append((i, j))
    return shadows
