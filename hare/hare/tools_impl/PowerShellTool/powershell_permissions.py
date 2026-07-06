"""PowerShell permission rule matching and command permission checks.

Port of: src/tools/PowerShellTool/powershellPermissions.ts

This module provides core permission matching logic for PowerShell commands,
including wildcard patterns, prefix rules, canonical alias resolution,
and module prefix handling — all case-insensitive.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal, Sequence

# ---------------------------------------------------------------------------
# PowerShell built-in aliases → canonical cmdlet name (lowercase)
# ---------------------------------------------------------------------------

_PS_ALIASES: dict[str, str] = {
    "rm": "remove-item", "del": "remove-item", "erase": "remove-item",
    "ri": "remove-item", "rd": "remove-item", "rmdir": "remove-item",
    "cat": "get-content", "gc": "get-content", "type": "get-content",
    "sc": "set-content", "ac": "add-content",
    "curl": "invoke-webrequest", "wget": "invoke-webrequest", "iwr": "invoke-webrequest",
    "irm": "invoke-restmethod",
    "echo": "write-output", "write": "write-output",
    "ls": "get-childitem", "dir": "get-childitem", "gci": "get-childitem",
    "cd": "set-location", "sl": "set-location", "chdir": "set-location",
    "cp": "copy-item", "copy": "copy-item", "cpi": "copy-item",
    "mv": "move-item", "move": "move-item", "mi": "move-item",
    "ps": "get-process", "gps": "get-process",
    "kill": "stop-process", "spps": "stop-process",
    "iex": "invoke-expression",
}

# Module-qualified prefix e.g. Microsoft.PowerShell.Management\Remove-Item
_MODULE_RE = re.compile(r"^[\w.]+\\(.+)$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Rule types — same structure as BashTool bash_permissions.py
# ---------------------------------------------------------------------------

@dataclass
class PrefixRule:
    type: Literal["prefix"] = "prefix"
    prefix: str = ""

@dataclass
class ExactRule:
    type: Literal["exact"] = "exact"
    command: str = ""

@dataclass
class WildcardRule:
    type: Literal["wildcard"] = "wildcard"
    pattern: str = ""

PermissionRule = PrefixRule | ExactRule | WildcardRule


# ---------------------------------------------------------------------------
# Rule parsing & matching
# ---------------------------------------------------------------------------

def powershell_permission_rule(pattern: str) -> PermissionRule:
    """Parse a permission pattern string into a rule object (case-insensitive).

    Order matters: check endswith(':*') BEFORE wildcard, otherwise the
    literal '*' in the suffix incorrectly triggers the wildcard branch.
    """
    pattern = pattern.strip()
    if not pattern:
        return ExactRule(command="")
    if pattern.endswith(":*"):
        return PrefixRule(prefix=pattern[:-2].lower())
    if "*" in pattern or "?" in pattern:
        return WildcardRule(pattern=pattern.lower())
    return ExactRule(command=pattern.lower())


def match_wildcard_pattern(pattern: str, command: str, *, case_insensitive: bool = True) -> bool:
    """Match a command against a wildcard pattern (* and ? supported)."""
    flags = re.IGNORECASE if case_insensitive else 0
    regex = "^" + "".join(
        ".*" if ch == "*" else "." if ch == "?" else re.escape(ch) for ch in pattern
    ) + "$"
    return bool(re.match(regex, command, flags))


def strip_module_prefix(name: str) -> str:
    """Strip PowerShell module-qualified prefix (Module\\Cmdlet → Cmdlet)."""
    m = _MODULE_RE.match(name)
    return m.group(1) if m else name


def resolve_to_canonical(name: str) -> str:
    """Resolve a command name to its canonical cmdlet form (lowercase)."""
    low = name.lower()
    return _PS_ALIASES.get(low, low)


# ---------------------------------------------------------------------------
# Permission checking
# ---------------------------------------------------------------------------

def check_powershell_permission(
    command: str,
    rules: Sequence[str],
    *,
    is_allow: bool = True,
) -> dict[str, Any]:
    """Check if a PowerShell command matches any permission rules.

    Performs case-insensitive matching with canonical alias resolution
    and module-prefix stripping. Returns {'matched': True, 'rule': str, 'is_allow': bool}
    on match, or {'matched': False} otherwise.
    """
    cmd = command.strip()
    if not cmd:
        return {"matched": False}

    candidates = _generate_candidates(cmd)
    for rule_str in rules:
        rule = powershell_permission_rule(rule_str)
        for candidate in candidates:
            if _matches_rule(rule, candidate):
                return {"matched": True, "rule": rule_str, "is_allow": is_allow}
    return {"matched": False}


def _generate_candidates(command: str) -> list[str]:
    """Generate match candidates: raw, stripped, canonical, and combinations."""
    seen: set[str] = set()
    candidates: list[str] = []
    cmd_low = command.lower()
    if cmd_low not in seen:
        candidates.append(cmd_low)
        seen.add(cmd_low)

    parts = cmd_low.split(None, 1)
    if not parts:
        return candidates
    raw_name = parts[0]
    rest = " " + parts[1] if len(parts) > 1 else ""

    for transform in (
        lambda n: n,
        strip_module_prefix,
        resolve_to_canonical,
        lambda n: resolve_to_canonical(strip_module_prefix(n)),
    ):
        transformed = transform(raw_name)
        full = transformed + rest
        if full not in seen:
            candidates.append(full)
            seen.add(full)
    return candidates


def _matches_rule(rule: PermissionRule, candidate: str) -> bool:
    """Match a candidate against a rule. All comparisons are case-insensitive."""
    if isinstance(rule, ExactRule):
        return candidate == rule.command
    if isinstance(rule, PrefixRule):
        return candidate == rule.prefix or candidate.startswith(rule.prefix + " ")
    if isinstance(rule, WildcardRule):
        return match_wildcard_pattern(rule.pattern, candidate, case_insensitive=True)
    return False
