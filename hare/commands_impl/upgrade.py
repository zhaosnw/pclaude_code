"""
/upgrade command - check for updates and upgrade.

Port of: src/commands/upgrade/ (2 files)

Checks for new versions and performs upgrades.
In the headless SDK, checks the installed package version.
"""

from __future__ import annotations

from typing import Any

COMMAND_NAME = "upgrade"
DESCRIPTION = "Check for updates and upgrade"
ALIASES: list[str] = []


async def call(args: str, **context: Any) -> dict[str, Any]:
    """Check for updates or upgrade."""
    check_version = context.get("check_version")
    perform_upgrade = context.get("perform_upgrade")
    current_version = context.get("current_version", "unknown")

    arg = (args or "").strip().lower()

    if arg == "now" or arg == "yes":
        if perform_upgrade:
            try:
                result = await perform_upgrade()
                return {
                    "type": "text",
                    "value": f"Upgrade complete.\n\n{result.get('message', 'Upgraded successfully.')}",
                }
            except Exception as e:
                return {
                    "type": "text",
                    "value": f"Upgrade failed: {e}\n\nTry: pip install --upgrade hare",
                    "display": "system",
                }

        # Fallback: suggest pip upgrade
        return {
            "type": "text",
            "value": ("To upgrade, run:\n\n```bash\npip install --upgrade hare\n```"),
        }

    if check_version:
        try:
            latest = await check_version()
            if latest.get("update_available"):
                return {
                    "type": "text",
                    "value": (
                        f"**Current version:** {current_version}\n"
                        f"**Latest version:** {latest['version']}\n\n"
                        f"An update is available. Run `/upgrade now` to upgrade."
                    ),
                }
            return {
                "type": "text",
                "value": f"**Current version:** {current_version}\n\nYou're up to date.",
            }
        except Exception:
            pass

    return {
        "type": "text",
        "value": (
            f"**Current version:** {current_version}\n\n"
            "Check for updates:\n"
            "```bash\n"
            "pip install --upgrade hare\n"
            "```\n\n"
            "Or run `/upgrade now` to upgrade."
        ),
    }


def get_command_definition() -> dict[str, Any]:
    return {
        "type": "local",
        "name": COMMAND_NAME,
        "description": DESCRIPTION,
        "aliases": ALIASES,
        "argumentHint": "[now]",
        "call": call,
    }
