"""
Init verifiers — verify CLI tool prerequisites on first launch.

Port of: src/commands/init-verifiers.ts (262 lines)

Checks for: gh CLI for git/github workflows, git configuration.
"""

from __future__ import annotations

import os
import subprocess
from typing import Any


async def run_init_verifiers_command(check_all: bool = False) -> dict[str, Any]:
    """Run verifier checks for required CLI tools.

    Returns dict with checker_name -> { status, message }.
    Status: 'ok' | 'missing' | 'error'
    """
    results: dict[str, Any] = {}

    # Check gh CLI
    gh_status = _check_gh_cli()
    results["gh"] = gh_status

    # Check git config
    git_status = _check_git_config()
    results["git"] = git_status

    # Check CLAUDE.md
    claude_md_status = _check_claude_md()
    results["claude_md"] = claude_md_status

    return results


def _check_gh_cli() -> dict[str, Any]:
    """Check if gh CLI is available."""
    try:
        result = subprocess.run(
            ["gh", "--version"], capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            return {
                "status": "ok",
                "message": result.stdout.split("\n")[0]
                if result.stdout
                else "gh CLI installed",
            }
        return {
            "status": "error",
            "message": f"gh CLI returned code {result.returncode}",
        }
    except FileNotFoundError:
        return {
            "status": "missing",
            "message": "gh CLI not found. Install from https://cli.github.com/",
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


def _check_git_config() -> dict[str, Any]:
    """Check if git has user.name and user.email configured."""
    try:
        name = subprocess.run(
            ["git", "config", "--global", "user.name"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        email = subprocess.run(
            ["git", "config", "--global", "user.email"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if (
            name.returncode == 0
            and email.returncode == 0
            and name.stdout.strip()
            and email.stdout.strip()
        ):
            return {
                "status": "ok",
                "message": f"Git configured for {name.stdout.strip()} <{email.stdout.strip()}>",
            }
        missing = []
        if not name.stdout.strip():
            missing.append("user.name")
        if not email.stdout.strip():
            missing.append("user.email")
        return {
            "status": "missing",
            "message": f"Git config missing: {', '.join(missing)}. Run: git config --global user.name '...' / user.email '...'",
        }
    except FileNotFoundError:
        return {"status": "missing", "message": "git not found"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def _check_claude_md() -> dict[str, Any]:
    """Check if CLAUDE.md exists in project root."""
    cwd = os.getcwd()
    for name in ("CLAUDE.md", "CLAUDE.local.md", ".claude/CLAUDE.md"):
        if os.path.exists(os.path.join(cwd, name)):
            return {"status": "ok", "message": f"{name} found"}
    return {"status": "ok", "message": "No CLAUDE.md found (optional)"}
