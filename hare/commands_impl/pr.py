"""Port of: src/commands/pr/. Create or view pull requests."""

from __future__ import annotations
import asyncio
from typing import Any

COMMAND_NAME = "pr"
DESCRIPTION = "Create or view pull requests"
ALIASES: list[str] = []

_PR_HELP = """Usage: /pr [list|create|view|status|checkout]

Subcommands:
  list       List open pull requests for the current repository
  create     Show instructions for creating a pull request
  view       View details of a specific PR (gh pr view <number>)
  status     Show the status of the current branch's PR(s)
  checkout   Check out a PR locally (gh pr checkout <number>)

Examples:
  /pr                  # list open PRs (default)
  /pr create           # instructions to create a PR
  /pr view 42          # view PR #42
  /pr status           # check CI status for this branch's PR"""


def _detect_remote_url() -> str | None:
    """Detect the origin remote URL and return a PR creation link."""
    import subprocess
    import re

    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=5,
        )
        url = result.stdout.strip()
        if not url:
            return None
        # Convert git@github.com:owner/repo.git → https://github.com/owner/repo
        m = re.match(r"git@([^:]+):(.+?)(?:\.git)?$", url)
        if m:
            host, repo = m.groups()
            return f"https://{host}/{repo}"
        # Convert https://github.com/owner/repo.git → https://github.com/owner/repo
        if url.endswith(".git"):
            url = url[:-4]
        return url
    except Exception:
        return None


async def _run_gh_pr(args: str) -> str:
    """Run gh pr <args> and return stdout or an error string."""
    cmd_parts = ["gh", "pr"] + args.split()
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd_parts,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        output = stdout.decode("utf-8", errors="replace").strip()
        if output:
            return output
        err = stderr.decode("utf-8", errors="replace").strip()
        if err:
            return f"gh pr error: {err}"
        return "(no output)"
    except FileNotFoundError:
        return ""
    except Exception as e:
        return f"Error running gh pr: {e}"


async def call(args: list[str], context: Any) -> dict[str, Any]:
    """Handle /pr: list pull requests or show creation instructions."""
    subcmd = args[0].strip() if args else ""
    extra = " ".join(args[1:]) if len(args) > 1 else ""

    if subcmd == "help" or subcmd == "--help":
        return {"type": "text", "value": _PR_HELP}

    if subcmd == "create":
        remote_url = _detect_remote_url()
        current_branch = ""
        try:
            result = await asyncio.create_subprocess_exec(
                "git", "branch", "--show-current",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            out, _ = await result.communicate()
            current_branch = out.decode().strip()
        except Exception:
            pass

        lines = [
            "To create a pull request:\n",
            "1. Ensure all changes are committed and pushed:",
            f"   git push -u origin {current_branch or '<your-branch>'}",
            "",
            "2. Create the PR via the GitHub CLI:",
            "   gh pr create --title \"<title>\" --body \"<description>\"",
            "",
            "3. Or open the web interface:",
        ]
        if remote_url:
            lines.append(f"   {remote_url}/compare/{current_branch or 'main'}...{current_branch or '<your-branch>'}")
            lines.append(f"   {remote_url}/pull/new/{current_branch or '<your-branch>'}")
        else:
            lines.append("   https://github.com/<owner>/<repo>/compare/<branch>")
            lines.append("   https://github.com/<owner>/<repo>/pull/new/<branch>")
        return {"type": "text", "value": "\n".join(lines)}

    # Try gh pr CLI for all other subcommands
    gh_args = f"{subcmd} {extra}".strip()
    output = await _run_gh_pr(gh_args)
    if output:
        return {"type": "text", "value": output}

    # Fallback: gh not available
    remote_url = _detect_remote_url()
    if remote_url:
        return {
            "type": "text",
            "value": (
                f"GitHub CLI (gh) not found. Use /pr create for instructions.\n"
                f"Pull requests: {remote_url}/pulls"
            ),
        }
    return {
        "type": "text",
        "value": (
            "GitHub CLI (gh) not found and no git remote detected.\n"
            "Use /pr create for manual PR creation instructions."
        ),
    }
