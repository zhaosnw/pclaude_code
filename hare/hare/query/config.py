"""Per-query immutable configuration snapshot.

Port of: src/query/config.ts (line-by-line).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from hare.bootstrap.state import get_session_id
from hare.services.analytics.growthbook import (
    check_statsig_feature_gate_cached_may_be_stale,
)
from hare.app_types.ids import SessionId
from hare.utils.env_utils import is_env_truthy

# -- src/query/config.ts L6-14
#
# Immutable values snapshotted once at query() entry. Separating these from
# the per-iteration State struct and the mutable ToolUseContext makes future
# step() extraction tractable — a pure reducer can take (state, event, config)
# where config is plain data.
#
# Intentionally excludes feature() gates — those are tree-shaking boundaries
# and must stay inline at the guarded blocks for dead-code elimination.


# -- src/query/config.ts L15-27
@dataclass(frozen=True)
class _Gates:
    # Runtime gates (env/statsig). NOT feature() gates — see above.
    streaming_tool_execution: bool
    emit_tool_use_summaries: bool
    is_ant: bool
    fast_mode_enabled: bool


@dataclass(frozen=True)
class QueryConfig:
    session_id: SessionId
    gates: _Gates


# -- src/query/config.ts L29-46
def build_query_config() -> QueryConfig:
    return QueryConfig(
        session_id=get_session_id(),
        gates=_Gates(
            # Statsig — CACHED_MAY_BE_STALE already admits staleness, so
            # snapshotting once per query() call stays within the existing
            # contract.
            streaming_tool_execution=check_statsig_feature_gate_cached_may_be_stale(
                "tengu_streaming_tool_execution2",
            ),
            emit_tool_use_summaries=is_env_truthy(
                os.environ.get("CLAUDE_CODE_EMIT_TOOL_USE_SUMMARIES"),
            ),
            is_ant=os.environ.get("USER_TYPE") == "ant",
            # Inlined from fastMode.ts to avoid pulling its heavy module graph
            # (axios, settings, auth, model, oauth, config) into test shards
            # that didn't previously load it — changes init order and breaks
            # unrelated tests.
            fast_mode_enabled=not is_env_truthy(
                os.environ.get("CLAUDE_CODE_DISABLE_FAST_MODE"),
            ),
        ),
    )
