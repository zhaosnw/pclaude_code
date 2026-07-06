"""Initialize analytics and error log sinks (port of sinks.ts)."""

from __future__ import annotations

from hare.utils.error_log_sink import initialize_error_log_sink


def initialize_analytics_sink() -> None:
    """Idempotent analytics sink attachment — extend in services/analytics."""
    pass


def init_sinks() -> None:
    initialize_error_log_sink()
    initialize_analytics_sink()
