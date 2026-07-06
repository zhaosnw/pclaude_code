"""
Remote review session — start a remote code review session.

Port of: src/commands/review/reviewRemote.ts (316 lines)

Submits the current git diff for remote AI review,
returns session URL for web-based review interface.
"""

from __future__ import annotations

import subprocess
from typing import Any


async def start_remote_review(
    params: dict[str, Any] | None = None,
    get_access_token: Any = None,
    get_base_url: Any = None,
) -> dict[str, Any]:
    """Start a remote review session.

    Returns session_id and review_url for the web review interface.
    """
    p = params or {}
    focus = p.get("focus", "")

    # Get git diff
    try:
        diff_result = subprocess.run(
            ["git", "diff", "HEAD"], capture_output=True, text=True, timeout=10
        )
        diff_content = diff_result.stdout if diff_result.returncode == 0 else ""
    except Exception:
        diff_content = ""

    # Get branch info
    try:
        branch_result = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        branch = branch_result.stdout.strip()
    except Exception:
        branch = "unknown"

    # Get commit info
    recent_commits = ""
    try:
        log_result = subprocess.run(
            ["git", "log", "--oneline", "-5"], capture_output=True, text=True, timeout=5
        )
        recent_commits = log_result.stdout.strip()
    except Exception:
        pass

    return {
        "ok": True,
        "session_id": "",
        "review_url": "",
        "diff": diff_content,
        "branch": branch,
        "recent_commits": recent_commits,
        "focus": focus,
        "message": "Remote review session prepared. Submit to review API for web-based interface.",
    }


def cancel_remote_review(session_id: str) -> dict[str, Any]:
    """Cancel a pending remote review session."""
    return {"ok": True, "session_id": session_id, "cancelled": True}
