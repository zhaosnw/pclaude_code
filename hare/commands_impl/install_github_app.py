"""
/install-github-app command - install the GitHub App for CI/CD.

Port of: src/commands/install-github-app/ (13 files)

Initiates OAuth-based GitHub App installation for:
  - CI/CD workflows
  - Pull request management
  - GitHub Actions integration
"""

from __future__ import annotations

from typing import Any

COMMAND_NAME = "install-github-app"
DESCRIPTION = "Install the GitHub App for CI / workflows"
ALIASES: list[str] = []


async def call(args: str, **context: Any) -> dict[str, Any]:
    """Install or check GitHub App status."""
    get_github_app_status = context.get("get_github_app_status")
    start_github_app_install = context.get("start_github_app_install")
    setup_github_actions = context.get("setup_github_actions")

    if get_github_app_status:
        status = await get_github_app_status()
        if status.get("installed"):
            return {
                "type": "text",
                "value": (
                    "## GitHub App\n\n"
                    "**Status:** Installed\n"
                    f"**Installation ID:** {status.get('installation_id', 'unknown')}\n"
                    f"**Repositories:** {status.get('repo_count', 0)}\n\n"
                    "The GitHub App is active and monitoring your repositories."
                ),
            }

    if start_github_app_install:
        try:
            result = await start_github_app_install()
            return {
                "type": "text",
                "value": (
                    "## Install GitHub App\n\n"
                    f"Open this URL to install:\n\n"
                    f"  {result.get('install_url', '')}\n\n"
                    "The GitHub App enables:\n"
                    "- Automatic PR review\n"
                    "- CI/CD pipeline integration\n"
                    "- Repository management"
                ),
            }
        except Exception as e:
            return {
                "type": "text",
                "value": f"GitHub App installation failed: {e}",
                "display": "system",
            }

    if setup_github_actions:
        try:
            await setup_github_actions()
            return {
                "type": "text",
                "value": "GitHub Actions workflow configured successfully.",
            }
        except Exception as e:
            return {
                "type": "text",
                "value": f"Failed to configure GitHub Actions: {e}",
                "display": "system",
            }

    return {
        "type": "text",
        "value": (
            "## Install GitHub App\n\n"
            "To install the GitHub App, use the Claude Code CLI:\n\n"
            "```bash\n"
            "claude /install-github-app\n"
            "```\n\n"
            "The GitHub App provides:\n"
            "- Automated PR review and management\n"
            "- CI/CD workflow integration\n"
            "- Repository-level settings"
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
