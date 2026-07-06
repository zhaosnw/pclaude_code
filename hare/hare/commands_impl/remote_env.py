"""
/remote-env command - manage remote environments.

Port of: src/commands/remote-env/ (2 files)

Lists and manages remote bridge environments.
"""

from __future__ import annotations

from typing import Any

COMMAND_NAME = "remote-env"
DESCRIPTION = "Manage remote bridge environments"
ALIASES: list[str] = []


async def call(args: str, **context: Any) -> dict[str, Any]:
    """List or manage remote environments."""
    get_remote_environments = context.get("get_remote_environments")
    register_remote_env = context.get("register_remote_env")

    # Check if we're registering a new env
    arg = (args or "").strip()
    if arg and register_remote_env:
        try:
            result = await register_remote_env(arg)
            return {
                "type": "text",
                "value": f"Registered remote environment: {result.get('name', arg)}",
            }
        except Exception as e:
            return {
                "type": "text",
                "value": f"Failed to register remote environment: {e}",
                "display": "system",
            }

    # List environments
    if get_remote_environments:
        envs = await get_remote_environments()
    else:
        envs = []

    if not envs:
        return {
            "type": "text",
            "value": "No remote environments configured.\n\nUse `/remote-env <name>` to register one.",
        }

    lines = ["## Remote Environments", ""]
    for env in envs:
        name = env.get("name", "unknown")
        status = env.get("status", "unknown")
        lines.append(f"- **{name}** ({status})")

    return {"type": "text", "value": "\n".join(lines)}


def get_command_definition() -> dict[str, Any]:
    return {
        "type": "local",
        "name": COMMAND_NAME,
        "description": DESCRIPTION,
        "aliases": ALIASES,
        "argumentHint": "[name]",
        "call": call,
    }
