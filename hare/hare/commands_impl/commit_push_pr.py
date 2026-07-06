"""
/commit-push-pr command - commit, push, and open a PR via AI workflow.

Port of: src/commands/commit-push-pr.ts

This is a PROMPT-based command that injects the full git workflow context
into the conversation so the AI can:
  1. Create a new branch
  2. Create a commit
  3. Push to origin
  4. Create or update a PR with gh pr create/edit
  5. Optionally post to Slack
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

COMMAND_NAME = "commit-push-pr"
DESCRIPTION = "Commit, push, and open a PR"
ALIASES: list[str] = []

ALLOWED_TOOLS = [
    "Bash(git checkout --branch:*)",
    "Bash(git checkout -b:*)",
    "Bash(git add:*)",
    "Bash(git status:*)",
    "Bash(git push:*)",
    "Bash(git commit:*)",
    "Bash(gh pr create:*)",
    "Bash(gh pr edit:*)",
    "Bash(gh pr view:*)",
    "Bash(gh pr merge:*)",
    "ToolSearch",
    "mcp__slack__send_message",
    "mcp__claude_ai_Slack__slack_send_message",
]


def get_attribution_texts() -> dict[str, str]:
    if os.environ.get("USER_TYPE") == "ant":
        return {"commit": "", "pr": ""}
    return {
        "commit": "\n\nCo-Authored-By: Claude Code <noreply@anthropic.com>",
        "pr": "\n\n🤖 Generated with [Claude Code](https://claude.com/claude-code)",
    }


def is_undercover() -> bool:
    return os.environ.get("USER_TYPE") == "ant" and os.environ.get("UNDERCOVER") == "1"


def get_undercover_instructions() -> str:
    return (
        "IMPORTANT: You are operating in undercover mode. "
        "Do NOT include any attribution or co-authored-by text. "
        "Make the commit appear as if it was created by a human.\n"
    )


async def get_default_branch() -> str:
    """Get the default branch for the repo."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "rev-parse",
            "--abbrev-ref",
            "origin/HEAD",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        branch = stdout.decode().strip()
        if branch:
            return branch.replace("origin/", "")
    except Exception:
        pass
    return "main"


def get_prompt_content(default_branch: str, pr_attribution: str | None = None) -> str:
    """Build the prompt content for commit-push-pr."""
    attributions = get_attribution_texts()
    commit_attribution = attributions.get("commit", "")
    default_pr_attribution = attributions.get("pr", "")
    effective_pr_attribution = (
        pr_attribution if pr_attribution is not None else default_pr_attribution
    )

    safe_user = os.environ.get("SAFEUSER", "")
    username = os.environ.get("USER", "")

    prefix = ""
    reviewer_arg = " and `--reviewer anthropics/claude-code`"
    add_reviewer_arg = " (and add `--add-reviewer anthropics/claude-code`)"
    changelog_section = """

## Changelog
<!-- CHANGELOG:START -->
[If this PR contains user-facing changes, add a changelog entry here. Otherwise, remove this section.]
<!-- CHANGELOG:END -->"""
    slack_step = """

5. After creating/updating the PR, check if the user's CLAUDE.md mentions posting to Slack channels. If it does, use ToolSearch to search for "slack send message" tools. If ToolSearch finds a Slack tool, ask the user if they'd like you to post the PR URL to the relevant Slack channel. Only post if the user confirms. If ToolSearch returns no results or errors, skip this step silently—do not mention the failure, do not attempt workarounds, and do not try alternative approaches."""

    if os.environ.get("USER_TYPE") == "ant" and is_undercover():
        prefix = get_undercover_instructions() + "\n"
        reviewer_arg = ""
        add_reviewer_arg = ""
        changelog_section = ""
        slack_step = ""

    return f"""{prefix}## Context

- `SAFEUSER`: {safe_user}
- `whoami`: {username}
- `git status`: !`git status`
- `git diff HEAD`: !`git diff HEAD`
- `git branch --show-current`: !`git branch --show-current`
- `git diff {default_branch}...HEAD`: !`git diff {default_branch}...HEAD`
- `gh pr view --json number 2>/dev/null || true`: !`gh pr view --json number 2>/dev/null || true`

## Git Safety Protocol

- NEVER update the git config
- NEVER run destructive/irreversible git commands (like push --force, hard reset, etc) unless the user explicitly requests them
- NEVER skip hooks (--no-verify, --no-gpg-sign, etc) unless the user explicitly requests it
- NEVER run force push to main/master, warn the user if they request it
- Do not commit files that likely contain secrets (.env, credentials.json, etc)
- Never use git commands with the -i flag (like git rebase -i or git add -i) since they require interactive input which is not supported

## Your task

Analyze all changes that will be included in the pull request, making sure to look at all relevant commits (NOT just the latest commit, but ALL commits that will be included in the pull request from the git diff {default_branch}...HEAD output above).

Based on the above changes:
1. Create a new branch if on {default_branch} (use SAFEUSER from context above for the branch name prefix, falling back to whoami if SAFEUSER is empty, e.g., `username/feature-name`)
2. Create a single commit with an appropriate message using heredoc syntax{"".join(", ending with the attribution text shown in the example below" if commit_attribution else "")}:
```
git commit -m "$(cat <<'EOF'
Commit message here.{"".join(commit_attribution)}
EOF
)"
```
3. Push the branch to origin
4. If a PR already exists for this branch (check the gh pr view output above), update the PR title and body using `gh pr edit` to reflect the current diff{"".join(add_reviewer_arg)}. Otherwise, create a pull request using `gh pr create` with heredoc syntax for the body{"".join(reviewer_arg)}.
   - IMPORTANT: Keep PR titles short (under 70 characters). Use the body for details.
```
gh pr create --title "Short, descriptive title" --body "$(cat <<'EOF'
## Summary
<1-3 bullet points>

## Test plan
[Bulleted markdown checklist of TODOs for testing the pull request...]{"".join(changelog_section)}{"".join(effective_pr_attribution)}
EOF
)"
```

You have the capability to call multiple tools in a single response. You MUST do all of the above in a single message.{"".join(slack_step)}

Return the PR URL when you're done, so the user can see it."""


async def get_enhanced_pr_attribution(get_app_state_fn: Any = None) -> str | None:
    """Get enhanced PR attribution text."""
    return get_attribution_texts().get("pr")


async def call(args: str, **context: Any) -> dict[str, Any]:
    """Execute the /commit-push-pr command.

    Returns prompt-based content that the AI will process.
    """
    get_app_state = context.get("get_app_state")
    execute_shell_commands_in_prompt = context.get("execute_shell_commands_in_prompt")

    # Get default branch and enhanced PR attribution
    default_branch = await get_default_branch()
    pr_attribution = await get_enhanced_pr_attribution(get_app_state)

    prompt_content = get_prompt_content(default_branch, pr_attribution)

    # Append user instructions if args provided
    trimmed_args = args.strip() if args else ""
    if trimmed_args:
        prompt_content += f"\n\n## Additional instructions from user\n\n{trimmed_args}"

    # Execute shell commands in prompt
    if execute_shell_commands_in_prompt:
        ctx = {
            **context,
            "getAppState": lambda: {
                **(get_app_state() if get_app_state else {}),
                "toolPermissionContext": {
                    **(get_app_state() if get_app_state else {}).get(
                        "toolPermissionContext", {}
                    ),
                    "alwaysAllowRules": {
                        **(get_app_state() if get_app_state else {})
                        .get("toolPermissionContext", {})
                        .get("alwaysAllowRules", {}),
                        "command": ALLOWED_TOOLS,
                    },
                },
            },
        }
        final_content = await execute_shell_commands_in_prompt(
            prompt_content, ctx, "/commit-push-pr"
        )
    else:
        final_content = prompt_content

    return [{"type": "text", "text": final_content}]


def get_command_definition() -> dict[str, Any]:
    return {
        "type": "prompt",
        "name": COMMAND_NAME,
        "description": DESCRIPTION,
        "aliases": ALIASES,
        "allowedTools": ALLOWED_TOOLS,
        "contentLength": len(get_prompt_content("main")),
        "progressMessage": "creating commit and PR",
        "source": "builtin",
        "getPromptForCommand": call,
    }
