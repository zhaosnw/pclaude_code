"""Emergency killswitch for analytics sinks. Port of: src/services/analytics/sinkKillswitch.ts"""

from __future__ import annotations

import os


def is_analytics_killed() -> bool:
    return os.environ.get("CLAUDE_CODE_ANALYTICS_KILL", "").lower() in (
        "1",
        "true",
        "yes",
    )
