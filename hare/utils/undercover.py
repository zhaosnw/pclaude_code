"""Undercover mode for public OSS contributions (port of undercover.ts)."""

from __future__ import annotations

import os

from hare.utils.env_utils import is_env_truthy


def is_undercover() -> bool:
    if os.environ.get("USER_TYPE") != "ant":
        return False
    if is_env_truthy(os.environ.get("CLAUDE_CODE_UNDERCOVER")):
        return True
    try:
        from hare.utils import commit_attribution as ca

        fn = getattr(ca, "get_repo_class_cached", None)
        if callable(fn):
            return fn() != "internal"
    except ImportError:
        pass
    return True


def get_undercover_instructions() -> str:
    if os.environ.get("USER_TYPE") != "ant":
        return ""
    return "## UNDERCOVER MODE — CRITICAL\n\nDo not leak internal codenames in commits or PRs.\n"


def should_show_undercover_auto_notice() -> bool:
    return is_undercover() and not is_env_truthy(
        os.environ.get("CLAUDE_CODE_UNDERCOVER_NOTICE_SEEN")
    )
