"""
Cost and usage tracking for the current session.

Port of: frontend/src/cost-tracker.ts

Tracks per-model token usage, estimated costs, API duration,
lines changed, and web search requests. Supports saving/restoring
cost state across session boundaries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from hare.services.api.logging import NonNullableUsage
from hare.utils.model_cost import (
    calculate_usd_cost,
    get_canonical_name,
)


@dataclass
class ModelUsage:
    """Per-model accumulated usage."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    web_search_requests: int = 0
    cost_usd: float = 0.0


@dataclass
class CostTracker:
    """Mutable session cost state."""

    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cache_creation_tokens: int = 0
    total_cache_read_tokens: int = 0
    total_api_duration: float = 0.0
    total_api_duration_without_retries: float = 0.0
    total_tool_duration: float = 0.0
    total_cost_usd: float = 0.0
    request_count: int = 0
    lines_added: int = 0
    lines_removed: int = 0
    web_search_requests: int = 0
    has_unknown_model: bool = False
    model_usage: dict[str, ModelUsage] = field(default_factory=dict)


_state = CostTracker()


# ---------------------------------------------------------------------------
# Accumulation helpers
# ---------------------------------------------------------------------------


def add_usage(usage: NonNullableUsage, model: str = "default") -> None:
    """Record token usage from an API call, computing cost via model pricing."""
    _state.total_input_tokens += usage.input_tokens
    _state.total_output_tokens += usage.output_tokens
    _state.total_cache_creation_tokens += usage.cache_creation_input_tokens
    _state.total_cache_read_tokens += usage.cache_read_input_tokens
    _state.request_count += 1

    ws = getattr(usage, "server_tool_use", None)
    ws_requests = (ws.get("web_search_requests", 0) if isinstance(ws, dict) else 0) if ws else 0
    _state.web_search_requests += ws_requests

    # Resolve "default" to the configured main-loop model (typically sonnet)
    resolved_model = _resolve_default_model(model)
    cost = calculate_usd_cost(resolved_model, usage)
    _state.total_cost_usd += cost

    _accumulate_model_usage(resolved_model, usage, cost)


def _resolve_default_model(model: str) -> str:
    """If *model* is ``"default"``, map to sonnet (standard pricing tier)."""
    if model == "default":
        return "claude-sonnet-4-6-20260301"
    return model


def _accumulate_model_usage(model: str, usage: Any, cost: float) -> None:
    """Track per-model usage."""
    short = get_canonical_name(model)
    mu = _state.model_usage.get(short)
    if mu is None:
        mu = ModelUsage()
        _state.model_usage[short] = mu

    inp = getattr(usage, "input_tokens", 0)
    out = getattr(usage, "output_tokens", 0)
    cr = getattr(usage, "cache_read_input_tokens", 0) or 0
    cc = getattr(usage, "cache_creation_input_tokens", 0) or 0

    mu.input_tokens += inp
    mu.output_tokens += out
    mu.cache_read_input_tokens += cr
    mu.cache_creation_input_tokens += cc
    mu.cost_usd += cost

    ws = getattr(usage, "server_tool_use", None)
    if isinstance(ws, dict):
        mu.web_search_requests += ws.get("web_search_requests", 0) or 0
    elif ws is not None:
        mu.web_search_requests += getattr(ws, "web_search_requests", 0) or 0


def add_api_duration(duration: float) -> None:
    """Record API call duration in seconds."""
    _state.total_api_duration += duration


def add_to_total_lines_changed(added: int = 0, removed: int = 0) -> None:
    """Accumulate lines added/removed across edits."""
    _state.lines_added += added
    _state.lines_removed += removed


def set_has_unknown_model_cost() -> None:
    """Mark that an unrecognised model was used (cost may be approximate)."""
    _state.has_unknown_model = True


# ---------------------------------------------------------------------------
# Getters
# ---------------------------------------------------------------------------


def get_total_cost() -> float:
    return _state.total_cost_usd


def get_total_input_tokens() -> int:
    return _state.total_input_tokens


def get_total_output_tokens() -> int:
    return _state.total_output_tokens


def get_total_cache_read_input_tokens() -> int:
    return _state.total_cache_read_input_tokens


def get_total_cache_creation_input_tokens() -> int:
    return _state.total_cache_creation_input_tokens


def get_total_web_search_requests() -> int:
    return _state.web_search_requests


def get_total_api_duration() -> float:
    return _state.total_api_duration


def get_total_lines_added() -> int:
    return _state.lines_added


def get_total_lines_removed() -> int:
    return _state.lines_removed


def get_model_usage() -> dict[str, ModelUsage]:
    """Return a copy of per-model usage (short name -> ModelUsage)."""
    return dict(_state.model_usage)


def has_unknown_model_cost() -> bool:
    return _state.has_unknown_model


def get_cost_summary() -> dict[str, Any]:
    """Return a snapshot suitable for serialisation."""
    return {
        "input_tokens": _state.total_input_tokens,
        "output_tokens": _state.total_output_tokens,
        "cache_creation_tokens": _state.total_cache_creation_tokens,
        "cache_read_tokens": _state.total_cache_read_tokens,
        "web_search_requests": _state.web_search_requests,
        "total_cost_usd": round(_state.total_cost_usd, 6),
        "total_api_duration": round(_state.total_api_duration, 2),
        "request_count": _state.request_count,
        "lines_added": _state.lines_added,
        "lines_removed": _state.lines_removed,
        "has_unknown_model": _state.has_unknown_model,
    }


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def _format_cost(amount: float, max_decimals: int = 4) -> str:
    if amount > 0.5:
        return f"${amount:.2f}"
    return f"${amount:.{max_decimals}f}"


def _format_number(n: int) -> str:
    return f"{n:,}"


def _format_model_usage() -> str:
    if not _state.model_usage:
        return "Usage:                 0 input, 0 output, 0 cache read, 0 cache write"

    lines: list[str] = ["Usage by model:"]
    for short_name, mu in sorted(_state.model_usage.items()):
        parts = [
            f"{_format_number(mu.input_tokens)} input",
            f"{_format_number(mu.output_tokens)} output",
            f"{_format_number(mu.cache_read_input_tokens)} cache read",
            f"{_format_number(mu.cache_creation_input_tokens)} cache write",
        ]
        if mu.web_search_requests:
            parts.append(f"{_format_number(mu.web_search_requests)} web search")
        parts.append(f"({_format_cost(mu.cost_usd)})")
        lines.append(f"{short_name + ':':>22} {'  '.join(parts)}")

    return "\n".join(lines)


def format_total_cost() -> str:
    """Return a human-readable multi-line cost summary.
    Returns empty string when no cost has been accumulated."""
    if _state.total_cost_usd <= 0 and _state.request_count == 0:
        return ""
    cost_display = _format_cost(_state.total_cost_usd)
    if _state.has_unknown_model:
        cost_display += " (costs may be inaccurate due to usage of unknown models)"

    def _fmt_dur(s: float) -> str:
        m, sec = divmod(int(s), 60)
        h, m = divmod(m, 60)
        if h:
            return f"{h}h {m}m {sec}s"
        if m:
            return f"{m}m {sec}s"
        return f"{sec}s"

    lines = [
        f"Total cost:            {cost_display}",
        f"Total duration (API):  {_fmt_dur(_state.total_api_duration)}",
        f"Total code changes:    {_state.lines_added} {'line' if _state.lines_added == 1 else 'lines'} added, "
        f"{_state.lines_removed} {'line' if _state.lines_removed == 1 else 'lines'} removed",
        _format_model_usage(),
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Persistence (save / restore / reset)
# ---------------------------------------------------------------------------


def save_current_session_costs(fps_metrics: Any = None) -> None:
    """Persist current-session cost data into project config so it survives
    across restarts.  Mirrors `saveCurrentSessionCosts` in cost-tracker.ts."""
    try:
        from hare.utils.config import get_current_project_config, save_current_project_config
        from hare.bootstrap.state import get_session_id
    except ImportError:
        return

    try:
        session_id = get_session_id()
    except Exception:
        session_id = ""

    model_usage_snapshot: dict[str, dict[str, Any]] = {}
    for model, mu in _state.model_usage.items():
        model_usage_snapshot[model] = {
            "inputTokens": mu.input_tokens,
            "outputTokens": mu.output_tokens,
            "cacheReadInputTokens": mu.cache_read_input_tokens,
            "cacheCreationInputTokens": mu.cache_creation_input_tokens,
            "webSearchRequests": mu.web_search_requests,
            "costUSD": mu.cost_usd,
        }

    def _update(current: dict[str, Any]) -> dict[str, Any]:
        current.update(
            lastCost=_state.total_cost_usd,
            lastAPIDuration=_state.total_api_duration,
            lastLinesAdded=_state.lines_added,
            lastLinesRemoved=_state.lines_removed,
            lastTotalInputTokens=_state.total_input_tokens,
            lastTotalOutputTokens=_state.total_output_tokens,
            lastTotalCacheCreationInputTokens=_state.total_cache_creation_tokens,
            lastTotalCacheReadInputTokens=_state.total_cache_read_tokens,
            lastTotalWebSearchRequests=_state.web_search_requests,
            lastModelUsage=model_usage_snapshot,
            lastSessionId=session_id,
        )
        if fps_metrics:
            current["lastFpsAverage"] = getattr(fps_metrics, "average_fps", None)
            current["lastFpsLow1Pct"] = getattr(fps_metrics, "low_1pct_fps", None)
        return current

    try:
        save_current_project_config(_update)
    except Exception:
        pass


def restore_cost_state_for_session(session_id: str) -> bool:
    """Try to restore cost state for *session_id* from project config.
    Returns True if state was restored."""
    try:
        from hare.utils.config import get_current_project_config
    except ImportError:
        return False

    cfg = get_current_project_config()
    if cfg.get("lastSessionId") != session_id:
        return False

    _state.total_cost_usd = cfg.get("lastCost", 0.0)
    _state.total_api_duration = cfg.get("lastAPIDuration", 0.0)
    _state.lines_added = cfg.get("lastLinesAdded", 0)
    _state.lines_removed = cfg.get("lastLinesRemoved", 0)
    _state.total_input_tokens = cfg.get("lastTotalInputTokens", 0)
    _state.total_output_tokens = cfg.get("lastTotalOutputTokens", 0)
    _state.total_cache_creation_tokens = cfg.get("lastTotalCacheCreationInputTokens", 0)
    _state.total_cache_read_tokens = cfg.get("lastTotalCacheReadInputTokens", 0)
    _state.web_search_requests = cfg.get("lastTotalWebSearchRequests", 0)

    model_snapshot: dict[str, dict[str, Any]] = cfg.get("lastModelUsage", {}) or {}
    _state.model_usage.clear()
    for model, data in model_snapshot.items():
        _state.model_usage[model] = ModelUsage(
            input_tokens=data.get("inputTokens", 0),
            output_tokens=data.get("outputTokens", 0),
            cache_read_input_tokens=data.get("cacheReadInputTokens", 0),
            cache_creation_input_tokens=data.get("cacheCreationInputTokens", 0),
            web_search_requests=data.get("webSearchRequests", 0),
            cost_usd=data.get("costUSD", 0.0),
        )
    return True


def reset_cost_state() -> None:
    """Discard all accumulated cost data (e.g. for a fresh session)."""
    global _state
    _state = CostTracker()


# Backward-compatible alias
def reset_cost_tracker() -> None:
    reset_cost_state()
