"""
/commit command - create a git commit via AI-generated message.

Port of: src/commands/commit.ts

This is a PROMPT-based command: instead of executing git directly,
it injects context (git status/diff/log) into the conversation so the
AI model can analyze changes and create the commit.

The TS command sets allowedTools to restrict the model to:
  Bash(git add:*), Bash(git status:*), Bash(git commit:*)
"""

from __future__ import annotations

import os
from typing import Any

COMMAND_NAME = "commit"
DESCRIPTION = "Create a git commit with a generated message"
ALIASES: list[str] = []

ALLOWED_TOOLS = [
    "Bash(git add:*)",
    "Bash(git status:*)",
    "Bash(git commit:*)",
]


def get_attribution_texts() -> dict[str, str]:
    """Get attribution texts based on environment."""
    if os.environ.get("USER_TYPE") == "ant":
        return {"commit": "", "pr": ""}
    return {
        "commit": "\n\nCo-Authored-By: Claude Code <noreply@anthropic.com>",
        "pr": "",
    }


def is_undercover() -> bool:
    """Check if undercover mode is active."""
    return os.environ.get("USER_TYPE") == "ant" and os.environ.get("UNDERCOVER") == "1"


def get_undercover_instructions() -> str:
    """Get undercover instructions prefix."""
    return (
        "IMPORTANT: You are operating in undercover mode. "
        "Do NOT include any attribution or co-authored-by text. "
        "Make the commit appear as if it was created by a human.\n"
    )


def get_prompt_content() -> str:
    """Build the prompt content that the AI model uses to create the commit.

    This mirrors the TS getPromptContent() function.
    """
    attributions = get_attribution_texts()
    commit_attribution = attributions.get("commit", "")

    prefix = ""
    if os.environ.get("USER_TYPE") == "ant" and is_undercover():
        prefix = get_undercover_instructions() + "\n"

    return f"""{prefix}## Context

- Current git status: !`git status`
- Current git diff (staged and unstaged changes): !`git diff HEAD`
- Current branch: !`git branch --show-current`
- Recent commits: !`git log --oneline -10`

## Git Safety Protocol

- NEVER update the git config
- NEVER skip hooks (--no-verify, --no-gpg-sign, etc) unless the user explicitly requests it
- CRITICAL: ALWAYS create NEW commits. NEVER use git commit --amend, unless the user explicitly requests it
- Do not commit files that likely contain secrets (.env, credentials.json, etc). Warn the user if they specifically request to commit those files
- If there are no changes to commit (i.e., no untracked files and no modifications), do not create an empty commit
- Never use git commands with the -i flag (like git rebase -i or git add -i) since they require interactive input which is not supported

## Your task

Based on the above changes, create a single git commit:

1. Analyze all staged changes and draft a commit message:
   - Look at the recent commits above to follow this repository's commit message style
   - Summarize the nature of the changes (new feature, enhancement, bug fix, refactoring, test, docs, etc.)
   - Ensure the message accurately reflects the changes and their purpose (i.e. "add" means a wholly new feature, "update" means an enhancement to an existing feature, "fix" means a bug fix, etc.)
   - Draft a concise (1-2 sentences) commit message that focuses on the "why" rather than the "what"

2. Stage relevant files and create the commit using HEREDOC syntax:
```
git commit -m "$(cat <<'EOF'
Commit message here.{"".join(commit_attribution)}
EOF
)"
```

You have the capability to call multiple tools in a single response. Stage and create the commit using a single message. Do not use any other tools or do anything else. Do not send any other text or messages besides these tool calls."""


async def call(args: str, **context: Any) -> dict[str, Any]:
    """Execute the /commit command.

    As a prompt-based command, this returns the prompt content
    that will be injected into the conversation for the AI to process.
    """
    # Get the prompt content
    prompt_content = get_prompt_content()

    # Execute shell commands embedded in the prompt (e.g., !`git status`)
    execute_shell_commands_in_prompt = context.get("execute_shell_commands_in_prompt")
    if execute_shell_commands_in_prompt:
        ctx = {
            **context,
            "getAppState": lambda: {
                **context.get("get_app_state", lambda: {})(),
                "toolPermissionContext": {
                    **(
                        context.get("get_app_state", lambda: {})().get(
                            "toolPermissionContext", {}
                        )
                    ),
                    "alwaysAllowRules": {
                        **(
                            context.get("get_app_state", lambda: {})()
                            .get("toolPermissionContext", {})
                            .get("alwaysAllowRules", {})
                        ),
                        "command": ALLOWED_TOOLS,
                    },
                },
            },
        }
        final_content = await execute_shell_commands_in_prompt(
            prompt_content, ctx, "/commit"
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
        "contentLength": 0,  # Dynamic content
        "progressMessage": "creating commit",
        "source": "builtin",
        "getPromptForCommand": call,
    }
