"""Human-readable permission explanations. Port of permissionExplainer.ts."""

from __future__ import annotations


def explain_permission_rule(pattern: str, tool: str) -> str:
    return f"Rule for {tool}: {pattern}"
