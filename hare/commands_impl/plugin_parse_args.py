"""
Plugin CLI argv parsing.

Port of: src/commands/plugin/parseArgs.ts
"""

from __future__ import annotations

from typing import Any


def parse_plugin_cli_args(argv: list[str]) -> dict[str, Any]:
    return {"argv": argv}
