"""Port of: src/commands/issue.ts. Show issue creation instructions and manage GitHub issues."""

from __future__ import annotations
import asyncio
import shutil
from typing import Any

COMMAND_NAME = "issue"
DESCRIPTION = "Create or view GitHub issues"
ALIASES: list[str] = []

_ISSUE_TEMPLATE = """## Description
<!-- A clear and concise description of the issue -->

## Steps to Reproduce
1.
2.
3.

## Expected Behavior
<!-- What you expected to happen -->

## Actual Behavior
<!-- What actually happened -->

## Environment
- OS:
- Version:
- Branch:

## Additional Context
<!-- Add any other context, logs, or screenshots -->"""


def _gh_available() -> bool:
    return shutil.which("gh") is not None


async def call(args: list[str], context: Any) -> dict[str, Any]:
    """Handle issue creation instructions and listing.

    Without arguments, lists recent open issues if gh CLI is present,
    otherwise shows issue creation guidance.  With 'create', prints a
    template.  With a URL or number, opens the issue.
    """
    subcmd = args[0].strip() if args else ""

    if subcmd == "create" or subcmd == "new":
        output = (
            "# Create a new GitHub Issue\n\n"
            "Use one of the following methods:\n\n"
            "### Via gh CLI\n"
            "```bash\n"
            'gh issue create --title "Your Title" --body "Description"\n'
            'gh issue create --web  # open browser editor\n'
            "```\n\n"
            "### Template\n"
            f"```markdown\n{_ISSUE_TEMPLATE}\n```\n\n"
            "### Tips\n"
            "- Use labels to categorize: `bug`, `enhancement`, `documentation`\n"
            "- Assign a milestone if applicable\n"
            "- Link related PRs with `Closes #NNN` in the description"
        )
        return {"type": "text", "value": output}

    if not _gh_available():
        output = (
            "# GitHub Issues\n\n"
            "The `gh` CLI is not installed. Install it from https://cli.github.com/\n\n"
            "### Create an issue via web\n"
            "Navigate to your repository on GitHub and click the **Issues** tab,\n"
            "then click **New Issue**.\n\n"
            "### Quick commands after installing gh\n"
            "```bash\n"
            'gh issue create --title "Title" --body "Body"\n'
            "gh issue list --state open\n"
            'gh issue view <number>\n'
            "```\n\n"
            "Run `/issue create` to see a full template."
        )
        return {"type": "text", "value": output}

    # gh is available – list recent open issues by default
    cmd = ["gh", "issue", "list", "--state", "open", "--limit", "20"]
    if subcmd == "all":
        cmd.append("--state")
        cmd.append("all")
    elif subcmd.isdigit():
        cmd = ["gh", "issue", "view", subcmd]
    elif subcmd == "mine":
        cmd = ["gh", "issue", "list", "--assignee", "@me", "--state", "open", "--limit", "20"]
    elif subcmd and subcmd not in ("list",):
        cmd = ["gh", "issue", "list", "--search", subcmd, "--state", "open", "--limit", "20"]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            err = stderr.decode("utf-8", errors="replace").strip()
            return {"type": "text", "value": f"Issue command failed:\n{err}"}
        output = stdout.decode("utf-8", errors="replace").strip() or "(no issues found)"
        return {"type": "text", "value": output}
    except FileNotFoundError:
        return {"type": "text", "value": "gh CLI not found. Install from https://cli.github.com/"}
    except Exception as e:
        return {"type": "text", "value": f"Issue command failed: {e}"}
