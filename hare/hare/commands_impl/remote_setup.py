"""
/web-setup (remote-setup) command - set up remote/web session.

Port of: src/commands/remote-setup/ (3 files: api.ts, index.ts, remoteSetup.tsx)

Initiates OAuth-based remote session setup.
In the TS CLI this is a multi-step wizard.
In the headless SDK, provides setup instructions and URL.
"""

from __future__ import annotations

from typing import Any

COMMAND_NAME = "web-setup"
DESCRIPTION = "Set up a remote / web session"
ALIASES = ["remote-setup"]


async def call(args: str, **context: Any) -> dict[str, Any]:
    """Initiate remote session setup."""
    get_remote_setup_url = context.get("get_remote_setup_url")
    start_remote_setup = context.get("start_remote_setup")

    if start_remote_setup:
        try:
            result = await start_remote_setup()
            if result.get("url"):
                return {
                    "type": "text",
                    "value": (
                        "## Remote Setup\n\n"
                        f"Open this URL to connect:\n\n"
                        f"  {result['url']}\n\n"
                        f"Session ID: {result.get('session_id', 'unknown')}\n\n"
                        "The session will be available at the URL above."
                    ),
                }
        except Exception as e:
            return {
                "type": "text",
                "value": f"Remote setup failed: {e}",
                "display": "system",
            }

    return {
        "type": "text",
        "value": (
            "## Remote Setup\n\n"
            "To set up a remote session, use the Claude Code CLI:\n\n"
            "```bash\n"
            "claude --remote\n"
            "```\n\n"
            "This will generate a URL you can open in your browser."
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
