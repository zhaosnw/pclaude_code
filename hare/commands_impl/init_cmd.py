"""
/init command - initialize project configuration.

Port of: src/commands/init.ts
"""

from __future__ import annotations

import os
from typing import Any

COMMAND_NAME = "init"
DESCRIPTION = "Initialize Hare configuration for this project"

INIT_PROMPT = """I'll help you set up Hare for this project. I'll:

1. Analyze your project structure and detect the tech stack
2. Create a HARE.md memory file with project conventions
3. Set up a .hare/settings.json with appropriate permissions

Let me start by examining your project..."""


async def call(args: str, **context: Any) -> dict[str, Any]:
    """Execute the /init command."""
    project_dir = context.get("project_dir", os.getcwd())
    hare_dir = os.path.join(project_dir, ".hare")
    memory_file = os.path.join(project_dir, "HARE.md")

    if os.path.isfile(memory_file):
        return {
            "type": "prompt",
            "value": "HARE.md already exists. Would you like me to review and update it?",
        }

    return {"type": "prompt", "value": INIT_PROMPT}


def get_command_definition() -> dict[str, Any]:
    return {
        "type": "prompt",
        "name": COMMAND_NAME,
        "description": DESCRIPTION,
        "call": call,
    }
