"""
Bash tool prompt generation.

Port of: src/tools/BashTool/prompt.ts
"""

from __future__ import annotations

import platform as _platform
from typing import Optional

BASH_TOOL_NAME = "Bash"
DEFAULT_TIMEOUT_MS = 120_000
MAX_TIMEOUT_MS = 600_000

SIMPLE_PROMPT = """Executes a given bash command in a persistent shell session with optional timeout.

## Usage
- The command argument is required. Prefer using absolute paths where possible.
- Commands touching files outside the workspace need user approval for security.
- You can run long-running commands with a timeout, and the output will be streamed back.
- The environment persists between calls (e.g. exported env vars, venv/nvm activations).
- For ANY commands that would require user interaction, ASSUME THE USER IS NOT AVAILABLE TO INTERACT and PASS THE NON-INTERACTIVE FLAGS (e.g. --yes for npx).
- For commands that use a pager, disable paging (for example, use git --no-pager or append | cat)
"""

SANDBOX_SECTION = """
## Sandbox mode
When sandbox is enabled:
- Commands run in an isolated environment with limited filesystem access
- Network access may be restricted
- File writes are limited to the working directory and its subdirectories
- Environment variables may be sanitized
"""

BACKGROUND_TASK_NOTES = """
## Background tasks
For long-running commands:
- You can run commands in the background using `run_in_background` parameter
- Background tasks are notified when they complete, no need to poll
- Use `sleep` sparingly — avoid polling loops in foreground shells
"""

SLEEP_AVOIDANCE = """
## Sleep and polling
- DO NOT use `sleep` in a retry loop to wait for commands to finish
- If you must check on a background process, use a single check command without sleep
- The shell is persistent — state carries over between calls naturally
"""

GIT_INSTRUCTIONS = """
## Git safety protocol
- NEVER update the git config
- NEVER run destructive/irreversible git commands (like push --force, hard reset, etc) unless the user explicitly requests them
- Avoid git commit --amend. ONLY use --amend when either (1) user explicitly requested amend OR (2) adding edits from pre-commit hook
- Before amending: ALWAYS check authorship (git log -1 --format='%an %ae')
- NEVER skip hooks (--no-verify, --no-gpg-sign, etc) unless the user explicitly requests it
- NEVER run force push to main/master, warn the user if they request it
- Avoid `git add -A` / `git add .` — prefer adding specific files
- Before committing: review changes with `git status` and `git diff --stat`
- Use descriptive commit messages following conventional commit format when possible

## Creating pull requests
For gh CLI operations:
- Use `gh pr create` with descriptive titles and body (via HEREDOC)
- Use --base flag when the default branch differs from main
"""

MULTI_COMMAND_GUIDANCE = """
## Multi-command parallelism
- You can run multiple independent commands by chaining with `&&` or `;`
- Commands separated by `;` run sequentially regardless of exit codes
- Commands with `&&` stop on first failure
- Use `&` only for truly independent background tasks
"""

PLATFORM_NOTES = f"""
## Platform
Current system: {_platform.system()} ({_platform.platform()})
- Be aware of platform-specific command differences
- On macOS, BSD variants of commands may differ from GNU/Linux (e.g., sed, grep)
- On Windows, use PowerShell-compatible commands or Git Bash
"""


def get_bash_prompt(
    *,
    sandbox_enabled: bool = False,
    include_commit_instructions: bool = True,
    platform: Optional[str] = None,
) -> str:
    """Build the full bash tool prompt."""
    parts = [
        SIMPLE_PROMPT,
        SLEEP_AVOIDANCE,
        BACKGROUND_TASK_NOTES,
        MULTI_COMMAND_GUIDANCE,
    ]

    if sandbox_enabled:
        parts.append(SANDBOX_SECTION)

    if include_commit_instructions:
        parts.append(GIT_INSTRUCTIONS)

    p = platform or _platform.system().lower()
    if p == "darwin":
        parts.append(PLATFORM_NOTES)

    return "\n".join(parts)


def get_default_timeout_ms() -> int:
    return DEFAULT_TIMEOUT_MS


def get_max_timeout_ms() -> int:
    return MAX_TIMEOUT_MS
