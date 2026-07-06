"""Runtime gate for agent teams / swarm features (`agentSwarmsEnabled.ts`)."""

from __future__ import annotations

import os
import sys

from hare.utils.env_utils import is_env_truthy


def _is_agent_teams_flag_set() -> bool:
    return "--agent-teams" in sys.argv


def is_agent_swarms_enabled() -> bool:
    if os.environ.get("USER_TYPE") == "ant":
        return True
    if (
        not is_env_truthy(os.environ.get("CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS"))
        and not _is_agent_teams_flag_set()
    ):
        return False
    try:
        from hare.services.analytics.growthbook import (  # type: ignore[import-not-found]
            get_feature_value_cached_may_be_stale,
        )

        if not get_feature_value_cached_may_be_stale("tengu_amber_flint", True):
            return False
    except Exception:
        return False
    return True
