"""
Apply permission updates to ToolPermissionContext.

Port of: src/utils/permissions/PermissionUpdate.ts (subset; extend as needed).
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from hare.app_types.permissions import ToolPermissionContext
from hare.utils.debug import log_for_debugging
from hare.utils.json_utils import json_stringify


def _rule_to_string(rule: Any) -> str:
    if hasattr(rule, "tool_name"):
        return f"{getattr(rule, 'tool_name', '')}:{getattr(rule, 'rule_content', '')}"
    return str(rule)


def extract_rules(updates: list[dict[str, Any]] | None) -> list[Any]:
    if not updates:
        return []
    out: list[Any] = []
    for u in updates:
        if u.get("type") == "addRules":
            out.extend(u.get("rules", []))
    return out


def has_rules(updates: list[dict[str, Any]] | None) -> bool:
    return len(extract_rules(updates)) > 0


def apply_permission_update(
    context: ToolPermissionContext,
    update: dict[str, Any],
) -> ToolPermissionContext:
    t = update.get("type")
    if t == "setMode":
        mode = update.get("mode")
        log_for_debugging(f"Applying permission update: Setting mode to '{mode}'")
        if isinstance(mode, str):
            return replace(context, mode=mode)  # type: ignore[arg-type]
        return context

    if t == "addRules":
        behavior = update.get("behavior")
        destination = update.get("destination")
        rules = update.get("rules", [])
        rule_strings = [_rule_to_string(r) for r in rules]
        log_for_debugging(
            f"Applying permission update: Adding {len(rules)} {behavior} rule(s) to '{destination}': "
            f"{json_stringify(rule_strings)}"
        )
        if behavior == "allow":
            m = dict(context.always_allow_rules)
            cur = list(m.get(destination, []))
            cur.extend(rule_strings)
            m[destination] = cur
            return replace(context, always_allow_rules=m)
        if behavior == "deny":
            m = dict(context.always_deny_rules)
            cur = list(m.get(destination, []))
            cur.extend(rule_strings)
            m[destination] = cur
            return replace(context, always_deny_rules=m)
        m = dict(context.always_ask_rules)
        cur = list(m.get(destination, []))
        cur.extend(rule_strings)
        m[destination] = cur
        return replace(context, always_ask_rules=m)

    return context


def apply_permission_updates(
    context: ToolPermissionContext,
    updates: list[dict[str, Any]],
) -> ToolPermissionContext:
    c = context
    for u in updates:
        c = apply_permission_update(c, u)
    return c
