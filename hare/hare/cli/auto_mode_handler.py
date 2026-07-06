"""
CLI handler for auto-mode subcommand — defaults, config, critique.

Port of: src/cli/handlers/autoMode.ts
"""

from __future__ import annotations

import json as _json
import os
from typing import Any


async def handle_auto_mode_command(args: dict[str, Any]) -> None:
    """Handle the 'auto-mode' CLI subcommand.

    Actions: defaults, config, critique
    """
    action = args.get("action", args.get("sub_action", "config"))

    if action == "defaults":
        _auto_mode_defaults(args)
    elif action == "config":
        _auto_mode_config(args)
    elif action == "critique":
        await _auto_mode_critique(args)
    else:
        print(f"Unknown auto-mode action: {action}")
        print("Available: defaults, config, critique")


def _auto_mode_defaults(args: dict[str, Any]) -> None:
    """Display default external auto mode rules."""
    defaults = {
        "rules": [
            {
                "name": "code_quality",
                "description": "Check for code quality issues",
                "enabled": True,
                "checks": ["unused_imports", "long_functions", "complex_conditionals"],
            },
            {
                "name": "security",
                "description": "Check for common security issues",
                "enabled": True,
                "checks": ["hardcoded_secrets", "sql_injection", "xss"],
            },
        ],
    }

    json_output = args.get("json", False)
    if json_output:
        print(_json.dumps(defaults, indent=2))
    else:
        print("Default auto-mode rules:")
        for rule in defaults["rules"]:
            status = "enabled" if rule["enabled"] else "disabled"
            print(f"  {rule['name']} ({status}): {rule['description']}")


def _auto_mode_config(args: dict[str, Any]) -> None:
    """Show or set auto-mode configuration."""
    config_path = os.path.join(os.path.expanduser("~"), ".claude", "auto_mode.json")

    # Show current config
    config = {}
    if os.path.isfile(config_path):
        try:
            with open(config_path, "r") as f:
                config = _json.load(f)
        except (_json.JSONDecodeError, OSError):
            pass

    # Merge defaults
    defaults = {
        "enabled": False,
        "max_turns": 100,
        "auto_compact": True,
        "rules": [],
    }
    merged = {**defaults, **config}

    json_output = args.get("json", False)
    if json_output:
        print(_json.dumps(merged, indent=2))
    else:
        print("Auto-mode configuration:")
        print(f"  Enabled: {merged['enabled']}")
        print(f"  Max turns: {merged['max_turns']}")
        print(f"  Auto compact: {merged['auto_compact']}")
        print(f"  Custom rules: {len(merged['rules'])}")


async def _auto_mode_critique(args: dict[str, Any]) -> None:
    """Critique user's auto-mode rules using AI analysis."""
    rule_file = args.get("file", args.get("rule_file", ""))

    if not rule_file:
        print("Usage: claude auto-mode critique <rule_file>")
        print("")
        print("Critiques user-defined auto-mode rules against defaults,")
        print("identifying conflicts, gaps, and improvement suggestions.")
        return

    if not os.path.isfile(rule_file):
        print(f"Rule file not found: {rule_file}")
        return

    try:
        with open(rule_file, "r") as f:
            rules = _json.load(f)
    except (_json.JSONDecodeError, OSError) as e:
        print(f"Failed to read rule file: {e}")
        return

    print(f"Analyzing auto-mode rules from: {rule_file}")
    print()
    print(
        f"  Rules found: {len(rules) if isinstance(rules, list) else 'invalid format'}"
    )
    print()
    print("AI critique requires the full Claude Code CLI with a model connection.")
    print("Run 'claude auto-mode critique <file>' in interactive mode for AI analysis.")
