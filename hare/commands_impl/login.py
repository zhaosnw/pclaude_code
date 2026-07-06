"""
/login command - authenticate with Anthropic API.

Port of: src/commands/login/ (2 files)

Initiates OAuth login flow or API key setup.
In the TS CLI this opens a browser for OAuth.
In the headless SDK, it validates the API key or guides to Console.
"""

from __future__ import annotations

import os
from typing import Any

COMMAND_NAME = "login"
DESCRIPTION = "Log in to your Anthropic account"
ALIASES: list[str] = []


async def call(args: str, **context: Any) -> dict[str, Any]:
    """Authenticate with Anthropic."""
    # Check if already authenticated
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        return {
            "type": "text",
            "value": "Already logged in. ANTHROPIC_API_KEY is set in environment.",
            "display": "system",
        }

    # Check for stored credentials
    check_login_status = context.get("check_login_status")
    if check_login_status:
        is_logged_in = await check_login_status()
        if is_logged_in:
            return {
                "type": "text",
                "value": "Already logged in. Credentials found.",
                "display": "system",
            }

    # In headless mode, guide the user
    return {
        "type": "text",
        "value": (
            "## Login Required\n\n"
            "Set your API key to authenticate:\n\n"
            "```bash\n"
            "export ANTHROPIC_API_KEY=sk-ant-...\n"
            "```\n\n"
            "Get your API key at: https://console.anthropic.com/\n\n"
            "For interactive OAuth login, use the Claude Code CLI:\n"
            "```bash\n"
            "claude login\n"
            "```"
        ),
    }


def get_command_definition() -> dict[str, Any]:
    return {
        "type": "local",
        "name": COMMAND_NAME,
        "description": DESCRIPTION,
        "aliases": ALIASES,
        "call": call,
    }
