"""
Git instruction toggles (settings-dependent).

Port of: src/utils/gitSettings.ts
"""

from __future__ import annotations

import os

from hare.utils.env_utils import is_env_defined_falsy, is_env_truthy


def should_include_git_instructions() -> bool:
    env_val = os.environ.get("CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS")
    if is_env_truthy(env_val):
        return False
    if is_env_defined_falsy(env_val):
        return True
    try:
        from hare.utils.settings.settings import get_initial_settings

        s = get_initial_settings()
        if isinstance(s, dict):
            return bool(s.get("include_git_instructions", True))
        return bool(getattr(s, "include_git_instructions", True))
    except ImportError:
        return True
