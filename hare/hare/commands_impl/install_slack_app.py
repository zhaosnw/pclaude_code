"""
/install-slack-app command - install the Slack integration.

Port of: src/commands/install-slack-app/ (2 files)

Initiates OAuth-based Slack app installation for:
  - Slack channel notifications
  - Message posting integration
  - Team collaboration features
"""

from __future__ import annotations

from typing import Any

COMMAND_NAME = "install-slack-app"
DESCRIPTION = "Install the Slack integration app"
ALIASES: list[str] = []


async def call(args: str, **context: Any) -> dict[str, Any]:
    """Install or check Slack App status."""
    get_slack_app_status = context.get("get_slack_app_status")
    start_slack_app_install = context.get("start_slack_app_install")

    if get_slack_app_status:
        status = await get_slack_app_status()
        if status.get("installed"):
            return {
                "type": "text",
                "value": (
                    "## Slack App\n\n"
                    "**Status:** Installed\n"
                    f"**Workspace:** {status.get('workspace', 'unknown')}\n"
                    f"**Channels:** {', '.join(status.get('channels', [])) or 'none configured'}\n\n"
                    "The Slack App can post PR updates and notifications."
                ),
            }

    if start_slack_app_install:
        try:
            result = await start_slack_app_install()
            return {
                "type": "text",
                "value": (
                    "## Install Slack App\n\n"
                    f"Open this URL to install:\n\n"
                    f"  {result.get('install_url', '')}\n\n"
                    "The Slack App enables:\n"
                    "- PR notifications in Slack channels\n"
                    "- Team collaboration\n"
                    "- Status updates"
                ),
            }
        except Exception as e:
            return {
                "type": "text",
                "value": f"Slack App installation failed: {e}",
                "display": "system",
            }

    return {
        "type": "text",
        "value": (
            "## Install Slack App\n\n"
            "To install the Slack App, use the Claude Code CLI:\n\n"
            "```bash\n"
            "claude /install-slack-app\n"
            "```\n\n"
            "The Slack App provides:\n"
            "- Post PR updates to Slack channels\n"
            "- Team notification integration\n"
            "- `/commit-push-pr` can auto-post to Slack"
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
