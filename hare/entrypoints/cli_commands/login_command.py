"""
CLI login command.

Port of: src/entrypoints/cli/loginCommand.ts
"""

from __future__ import annotations

import os
import json


async def run_login_command(api_key: str | None = None) -> bool:
    """Login with API key."""
    if not api_key:
        print(
            "Please provide an API key with --api-key or set ANTHROPIC_API_KEY env var"
        )
        return False
    config_dir = os.path.join(os.path.expanduser("~"), ".hare")
    os.makedirs(config_dir, exist_ok=True)
    creds_path = os.path.join(config_dir, "credentials.json")
    try:
        with open(creds_path, "w", encoding="utf-8") as f:
            json.dump({"apiKey": api_key}, f)
        print("Login successful")
        return True
    except OSError as e:
        print(f"Failed to save credentials: {e}")
        return False
