"""
Expand ${VAR} and ${VAR:-default} in MCP config strings.

Port of: src/services/mcp/envExpansion.ts
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass


@dataclass
class ExpandEnvResult:
    expanded: str
    missing_vars: list[str]


_VAR_RE = re.compile(r"\$\{([^}]+)\}")


def expand_env_vars_in_string(value: str) -> ExpandEnvResult:
    missing: list[str] = []

    def repl(match: re.Match[str]) -> str:
        var_content = match.group(1)
        parts = var_content.split(":-", 1)
        var_name = parts[0]
        default_value = parts[1] if len(parts) > 1 else None
        env_value = os.environ.get(var_name)
        if env_value is not None:
            return env_value
        if default_value is not None:
            return default_value
        missing.append(var_name)
        return match.group(0)

    expanded = _VAR_RE.sub(repl, value)
    return ExpandEnvResult(expanded=expanded, missing_vars=missing)
