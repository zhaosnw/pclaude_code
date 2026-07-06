"""
/logout command - log out of Anthropic account.

Port of: src/commands/logout/ (2 files)

Clears stored credentials and session tokens.
"""

from __future__ import annotations

import os
from typing import Any

COMMAND_NAME = "logout"
DESCRIPTION = "Log out of your Anthropic account"
ALIASES: list[str] = []


async def call(args: str, **context: Any) -> dict[str, Any]:
    """Clear stored credentials."""
    clear_credentials = context.get("clear_credentials")
    clear_session_tokens = context.get("clear_session_tokens")

    results = []

    if clear_credentials:
        try:
            await clear_credentials()
            results.append("Stored credentials cleared.")
        except Exception as e:
            results.append(f"Failed to clear stored credentials: {e}")

    if clear_session_tokens:
        try:
            await clear_session_tokens()
            results.append("Session tokens cleared.")
        except Exception as e:
            results.append(f"Failed to clear session tokens: {e}")

    if not results:
        # Nothing to clear - user is likely using env var auth
        if os.environ.get("ANTHROPIC_API_KEY"):
            return {
                "type": "text",
                "value": (
                    "Using ANTHROPIC_API_KEY from environment.\n"
                    "Unset the environment variable to log out:\n"
                    "```bash\nunset ANTHROPIC_API_KEY\n```"
                ),
                "display": "system",
            }
        return {
            "type": "text",
            "value": "Not logged in. No stored credentials found.",
            "display": "system",
        }

    return {
        "type": "text",
        "value": "Logged out successfully.\n\n" + "\n".join(results),
        "display": "system",
    }


def get_command_definition() -> dict[str, Any]:
    return {
        "type": "local",
        "name": COMMAND_NAME,
        "description": DESCRIPTION,
        "aliases": ALIASES,
        "call": call,
    }
