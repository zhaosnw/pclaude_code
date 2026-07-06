"""
Datadog metrics submission with batching and flush.

Port of: src/services/analytics/datadog.ts

Provides:
- Configuration-driven Datadog client (DD_API_KEY / DD_CLIENT_TOKEN / DD_SITE)
- Metric type support: COUNT, GAUGE, RATE, DISTRIBUTION
- Batched queuing with background periodic flush
- Graceful shutdown integration via _shutdown_datadog()
- Standard tags (platform, version, session_id, install_method, etc.)
- Analytics sink integration (convert analytics events → Datadog metrics)
- Killswitch awareness (analytics killswitch, sink enabled check)
- Retry / circuit-breaker for transient HTTP failures
"""

from __future__ import annotations

import asyncio
import json
import os
import platform as _platform
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Callable, Optional, Union

from hare.services.analytics.config import is_analytics_enabled
from hare.services.analytics.sink_killswitch import is_analytics_killed
from hare.constants.keys import get_datadog_client_token
from hare.utils.debug import log_for_debugging

# ---------------------------------------------------------------------------
# Metric types (aligned with Datadog API v2 MetricType enum)
# ---------------------------------------------------------------------------


class MetricType(IntEnum):
    """Datadog metric type identifiers for the v2 series API."""

    UNSPECIFIED = 0
    COUNT = 1
    RATE = 2
    GAUGE = 3
    DISTRIBUTION = 4


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


# Default flush interval in seconds
DEFAULT_FLUSH_INTERVAL = 30.0

# Maximum number of points per series in a single API call
MAX_SERIES_PER_FLUSH = 1000

# Maximum points across all series in a single API call
MAX_POINTS_PER_FLUSH = 5000

# Maximum retries for transient failures
MAX_FLUSH_RETRIES = 3

# Base delay between retries (seconds)
BASE_RETRY_DELAY = 1.0

# Circuit breaker: max consecutive failures before pausing
CIRCUIT_BREAKER_THRESHOLD = 5

# Cooldown after circuit breaker trips (seconds)
CIRCUIT_BREAKER_COOLDOWN = 120.0


def _env_bool(name: str, default: bool = False) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.lower() in ("1", "true", "yes", "on")


def _env_float(name: str, default: float) -> float:
    val = os.environ.get(name)
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _get_dd_api_key() -> str:
    """Get Datadog API key from environment (for metrics submission)."""
    return os.environ.get("DD_API_KEY") or os.environ.get("DATADOG_API_KEY") or ""


def _get_dd_site() -> str:
    """Get Datadog site from environment (default: datadoghq.com)."""
    return os.environ.get("DD_SITE") or os.environ.get("DATADOG_SITE") or "datadoghq.com"


def _get_dd_metrics_endpoint() -> str:
    """Get the Datadog metrics API endpoint."""
    base = os.environ.get("DD_API_HOST") or os.environ.get(
        "DATADOG_API_HOST"
    )
    if base:
        return f"{base.rstrip('/')}/api/v2/series"
    site = _get_dd_site()
    if site.startswith("http"):
        return f"{site.rstrip('/')}/api/v2/series"
    return f"https://api.{site}/api/v2/series"


def _get_flush_interval() -> float:
    return _env_float("DD_FLUSH_INTERVAL_SECONDS", DEFAULT_FLUSH_INTERVAL)


def _get_install_method() -> str:
    """Determine how hare was installed."""
    if os.environ.get("NPM_CONFIG_REGISTRY") or os.environ.get("npm_package_name"):
        return "npm"
    if os.environ.get("PIPX_HOME") or os.environ.get("PIPENV_ACTIVE"):
        return "pipx"
    if os.environ.get("CONDA_DEFAULT_ENV") or os.environ.get("CONDA_PREFIX"):
        return "conda"
    return "pip"


# ---------------------------------------------------------------------------
# Datadog metric point
# ---------------------------------------------------------------------------


@dataclass
class MetricPoint:
    """A single metric data point ready for submission."""

    metric: str
    value: float
    type: MetricType
    tags: list[str] = field(default_factory=list)
    timestamp: int = 0  # Unix seconds; 0 = server-side timestamp

    def to_api_dict(self) -> dict[str, Any]:
        """Convert to Datadog API v2 series payload."""
        return {
            "metric": self.metric,
            "type": int(self.type),
            "points": [{"value": self.value, "timestamp": self.timestamp}],
            "tags": sorted(set(self.tags)),
        }


# ---------------------------------------------------------------------------
# Metric name validation & normalization
# ---------------------------------------------------------------------------

# Valid metric name pattern: alphanumeric, dots, underscores, hyphens
import re as _re

_METRIC_NAME_RE = _re.compile(r"^[a-zA-Z][a-zA-Z0-9._\-]{0,199}$")
_METRIC_NAME_INVALID_RE = _re.compile(r"[^a-zA-Z0-9._\-]")
_METRIC_NAME_COLLAPSE_RE = _re.compile(r"\.{2,}")


def normalize_metric_name(name: str) -> str:
    """Normalize a metric name to meet Datadog requirements.

    Rules:
    - Must start with a letter
    - Only ``[a-zA-Z0-9._-]`` allowed
    - Max 200 characters
    - Consecutive dots collapsed to one
    """
    name = name.strip()
    name = _METRIC_NAME_INVALID_RE.sub("_", name)
    name = _METRIC_NAME_COLLAPSE_RE.sub(".", name)
    # Ensure starts with a letter
    if name and not name[0].isalpha():
        name = "hare." + name.lstrip("._-")
    # Truncate to 200 chars
    if len(name) > 200:
        # Preserve the prefix — keep first 100 and last 96
        name = name[:100] + "..." + name[-96:]
    return name


def validate_metric_name(name: str) -> bool:
    """Check whether a metric name is valid for Datadog."""
    return bool(_METRIC_NAME_RE.match(name))


# ---------------------------------------------------------------------------
# Queue backpressure
# ---------------------------------------------------------------------------

# Maximum number of points allowed in the queue before backpressure kicks in
MAX_QUEUE_SIZE = 10_000

# When the queue is full, this fraction of oldest points is dropped
BACKPRESSURE_DROP_FRACTION = 0.2


def _apply_queue_backpressure() -> int:
    """Enforce max queue size, dropping oldest points if needed.

    Returns the number of points dropped.
    """
    if len(_metrics_queue) <= MAX_QUEUE_SIZE:
        return 0
    to_drop = int(len(_metrics_queue) * BACKPRESSURE_DROP_FRACTION)
    dropped = len(_metrics_queue) - MAX_QUEUE_SIZE + to_drop
    if dropped > 0:
        del _metrics_queue[:dropped]
        log_for_debugging(
            f"datadog: backpressure dropped {dropped} oldest metric points "
            f"(queue was {len(_metrics_queue) + dropped}, max {MAX_QUEUE_SIZE})"
        )
    return max(0, dropped)


# ---------------------------------------------------------------------------
# Tag cardinality guard
# ---------------------------------------------------------------------------

# Maximum number of distinct tag values tracked per metric name
MAX_TAG_CARDINALITY = 1000

# Per-metric tag value set to detect cardinality explosion
_tag_cardinality: dict[str, set[str]] = {}


def _guard_tag_cardinality(metric: str, tags: list[str]) -> list[str]:
    """Filter out tags that would cause cardinality explosion.

    For each metric name, tracks distinct tag values. Once the limit is
    exceeded, subsequent novel values for that tag key are replaced with
    ``other`` to bound cardinality.
    """
    if metric not in _tag_cardinality:
        _tag_cardinality[metric] = set()
    known = _tag_cardinality[metric]

    safe_tags: list[str] = []
    for tag in tags:
        if ":" not in tag:
            safe_tags.append(tag)
            continue
        key, value = tag.split(":", 1)
        full = tag
        if len(known) < MAX_TAG_CARDINALITY:
            known.add(full)
            safe_tags.append(tag)
        elif full in known:
            safe_tags.append(tag)
        else:
            # Cardinality limit reached — squelch to "other"
            safe_tags.append(f"{key}:other")
    return safe_tags


# ---------------------------------------------------------------------------
# Internal state
# ---------------------------------------------------------------------------


_metrics_queue: list[MetricPoint] = []
_tags_provider: Optional[Callable[[], list[str]]] = None
_config_provider: Optional[Callable[[], dict[str, Any]]] = None
_flush_task: Optional[asyncio.Task] = None
_flush_lock = asyncio.Lock()
_consecutive_failures = 0
_circuit_open_until: float = 0.0
_initialized = False
_total_metrics_sent: int = 0
_total_metrics_dropped: int = 0
_total_flush_errors: int = 0
_last_flush_time: float = 0.0
_last_flush_success: bool = True


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------


def build_base_tags(
    *,
    extra: Optional[dict[str, str]] = None,
    application: str = "hare",
) -> list[str]:
    """Build standard tag list from current environment.

    Tags follow the format ``key:value``.
    """
    tags: list[str] = [
        f"platform:{_platform.system().lower()}",
        f"application:{application}",
    ]

    # Version tag
    try:
        from hare.services.api.client import VERSION
    except ImportError:
        VERSION = "0.0.0"
    tags.append(f"version:{VERSION}")

    # Install method
    install = _get_install_method()
    if install:
        tags.append(f"install_method:{install}")

    # Session id (privacy-safe truncated)
    try:
        from hare.bootstrap.state import get_session_id

        sid = get_session_id()
        if sid:
            tags.append(f"session_id:{sid[:12]}")
        from hare.bootstrap.state import get_is_non_interactive_session

        if get_is_non_interactive_session():
            tags.append("session_type:non_interactive")
        else:
            tags.append("session_type:interactive")
    except Exception:
        pass

    # Python version
    import sys

    tags.append(f"python_version:{sys.version_info.major}.{sys.version_info.minor}")

    # Architecture
    import platform

    tags.append(f"arch:{platform.machine()}")

    # Extra tags passed by caller
    if extra:
        for k, v in extra.items():
            safe_k = k.lower().replace(" ", "_")
            safe_v = str(v).lower().replace(" ", "_")
            if safe_k and safe_v:
                tags.append(f"{safe_k}:{safe_v}")

    return tags


def set_tags_provider(provider: Callable[[], list[str]]) -> None:
    """Install a dynamic tags provider (called at flush time)."""
    global _tags_provider
    _tags_provider = provider


def set_config_provider(provider: Callable[[], dict[str, Any]]) -> None:
    """Install a configuration provider for dynamic reconfiguration."""
    global _config_provider
    _config_provider = provider


# ---------------------------------------------------------------------------
# Enablement checks
# ---------------------------------------------------------------------------


def datadog_sink_enabled() -> bool:
    """Check whether the Datadog sink should be active.

    Returns True when:
    - Analytics is globally enabled (not disabled/killed)
    - A Datadog API key OR client token is configured
    - Not explicitly disabled via DD_METRICS_DISABLED
    """
    if _env_bool("DD_METRICS_DISABLED", False):
        return False
    if is_analytics_killed():
        return False
    # We allow the sink to be enabled with either API key or client token.
    # If only a client token is present, we use the RUM/logs endpoint path.
    has_key = bool(_get_dd_api_key())
    has_token = bool(get_datadog_client_token())
    if not has_key and not has_token:
        return False
    return is_analytics_enabled()


# ---------------------------------------------------------------------------
# Core metric submission
# ---------------------------------------------------------------------------


async def send_datadog_metric(
    name: str,
    value: float,
    tags: Optional[dict[str, str]] = None,
    *,
    metric_type: MetricType = MetricType.COUNT,
    timestamp: int = 0,
) -> None:
    """Enqueue a Datadog metric for batched submission.

    Args:
        name: Metric name (e.g. ``hare.session.started``).
        value: Numeric metric value.
        tags: Optional tag dictionary; keys and values are lowercased and
              joined as ``key:value`` strings.
        metric_type: The Datadog metric type (COUNT by default).
        timestamp: Unix seconds; 0 means the Datadog server assigns a
                   timestamp.
    """
    if not datadog_sink_enabled():
        return
    safe_name = normalize_metric_name(name)
    tag_list: list[str] = []
    if tags:
        for k, v in tags.items():
            safe_k = str(k).lower().replace(" ", "_")
            safe_v = str(v).lower().replace(" ", "_")
            if safe_k and safe_v:
                tag_list.append(f"{safe_k}:{safe_v}")
    tag_list = _guard_tag_cardinality(safe_name, tag_list)
    point = MetricPoint(
        metric=safe_name,
        value=float(value),
        type=metric_type,
        tags=tag_list,
        timestamp=timestamp,
    )
    _metrics_queue.append(point)
    _apply_queue_backpressure()


def send_datadog_metric_sync(
    name: str,
    value: float,
    tags: Optional[dict[str, str]] = None,
    *,
    metric_type: MetricType = MetricType.COUNT,
    timestamp: int = 0,
) -> None:
    """Synchronous wrapper around ``send_datadog_metric``.

    Safe to call from synchronous code — schedules the metric on any
    running event loop or queues locally. If no loop is running the
    point is still queued; it will be flushed later.
    """
    if not datadog_sink_enabled():
        return
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(
            send_datadog_metric(
                name, value, tags, metric_type=metric_type, timestamp=timestamp
            )
        )
    except RuntimeError:
        # No running loop — just append directly (will be flushed later)
        safe_name = normalize_metric_name(name)
        tag_list: list[str] = []
        if tags:
            for k, v in tags.items():
                safe_k = str(k).lower().replace(" ", "_")
                safe_v = str(v).lower().replace(" ", "_")
                if safe_k and safe_v:
                    tag_list.append(f"{safe_k}:{safe_v}")
        tag_list = _guard_tag_cardinality(safe_name, tag_list)
        _metrics_queue.append(
            MetricPoint(
                metric=safe_name,
                value=float(value),
                type=metric_type,
                tags=tag_list,
                timestamp=timestamp,
            )
        )
        _apply_queue_backpressure()


# ---------------------------------------------------------------------------
# Convenience helpers for common metric types (async)
# ---------------------------------------------------------------------------


async def increment(
    name: str,
    by: float = 1.0,
    tags: Optional[dict[str, str]] = None,
    **kwargs: Any,
) -> None:
    """Increment a COUNT metric."""
    await send_datadog_metric(name, by, tags, metric_type=MetricType.COUNT, **kwargs)


async def gauge(
    name: str,
    value: float,
    tags: Optional[dict[str, str]] = None,
    **kwargs: Any,
) -> None:
    """Record a GAUGE metric."""
    await send_datadog_metric(name, value, tags, metric_type=MetricType.GAUGE, **kwargs)


async def histogram(
    name: str,
    value: float,
    tags: Optional[dict[str, str]] = None,
    **kwargs: Any,
) -> None:
    """Record a DISTRIBUTION metric."""
    await send_datadog_metric(
        name, value, tags, metric_type=MetricType.DISTRIBUTION, **kwargs
    )


async def rate(
    name: str,
    value: float,
    tags: Optional[dict[str, str]] = None,
    **kwargs: Any,
) -> None:
    """Record a RATE metric."""
    await send_datadog_metric(name, value, tags, metric_type=MetricType.RATE, **kwargs)


# ---------------------------------------------------------------------------
# Convenience helpers for common metric types (sync)
# ---------------------------------------------------------------------------


def increment_sync(
    name: str,
    by: float = 1.0,
    tags: Optional[dict[str, str]] = None,
    **kwargs: Any,
) -> None:
    """Synchronous increment of a COUNT metric."""
    send_datadog_metric_sync(name, by, tags, metric_type=MetricType.COUNT, **kwargs)


def gauge_sync(
    name: str,
    value: float,
    tags: Optional[dict[str, str]] = None,
    **kwargs: Any,
) -> None:
    """Synchronous GAUGE metric."""
    send_datadog_metric_sync(name, value, tags, metric_type=MetricType.GAUGE, **kwargs)


def histogram_sync(
    name: str,
    value: float,
    tags: Optional[dict[str, str]] = None,
    **kwargs: Any,
) -> None:
    """Synchronous DISTRIBUTION metric."""
    send_datadog_metric_sync(
        name, value, tags, metric_type=MetricType.DISTRIBUTION, **kwargs
    )


def rate_sync(
    name: str,
    value: float,
    tags: Optional[dict[str, str]] = None,
    **kwargs: Any,
) -> None:
    """Synchronous RATE metric."""
    send_datadog_metric_sync(name, value, tags, metric_type=MetricType.RATE, **kwargs)


# ---------------------------------------------------------------------------
# HTTP submission
# ---------------------------------------------------------------------------


def _build_http_headers() -> dict[str, str]:
    """Build HTTP headers for the Datadog API request."""
    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    api_key = _get_dd_api_key()
    if api_key:
        headers["DD-API-KEY"] = api_key
    else:
        # Fall back to client token (for RUM/logs endpoints)
        client_token = get_datadog_client_token()
        if client_token:
            headers["DD-APPLICATION-KEY"] = client_token
    return headers


def _get_proxy_handler() -> Optional[urllib.request.ProxyHandler]:
    """Build a urllib proxy handler if proxy is configured."""
    try:
        from hare.utils.http import build_proxy_handler
        return build_proxy_handler()
    except ImportError:
        pass
    return None


def _enrich_points(points: list[MetricPoint]) -> list[MetricPoint]:
    """Add base tags to every point if a tags provider is installed."""
    if _tags_provider is None:
        return points
    try:
        base = _tags_provider()
    except Exception:
        base = []
    if not base:
        return points
    for point in points:
        seen = set(point.tags)
        for tag in base:
            if tag not in seen:
                point.tags.append(tag)
    return points


def _build_series_payload(points: list[MetricPoint]) -> dict[str, Any]:
    """Build the Datadog API v2 series payload."""
    enriched = _enrich_points(points)
    series = [p.to_api_dict() for p in enriched]
    return {"series": series}


async def _flush_to_datadog(points: list[MetricPoint]) -> bool:
    """Send a batch of metric points to Datadog.

    Returns True on success, False on failure.
    """
    if not points:
        return True
    global _consecutive_failures, _circuit_open_until, \
        _total_metrics_dropped, _last_flush_time, _last_flush_success

    # Circuit breaker check
    now = time.time()
    if now < _circuit_open_until:
        log_for_debugging("datadog: circuit breaker open, dropping flush")
        return False

    endpoint = _get_dd_metrics_endpoint()
    headers = _build_http_headers()
    payload = _build_series_payload(points)
    body = json.dumps(payload).encode("utf-8")

    def _http_post():
        req = urllib.request.Request(
            endpoint, data=body, method="POST"
        )
        for k, v in headers.items():
            req.add_header(k, v)

        handlers: list[Any] = []
        proxy_handler = _get_proxy_handler()
        if proxy_handler:
            handlers.append(proxy_handler)

        opener = urllib.request.build_opener(*handlers)
        resp = opener.open(req, timeout=15.0)
        status = resp.status
        resp_body = resp.read().decode("utf-8")
        return status, resp_body

    for attempt in range(1, MAX_FLUSH_RETRIES + 1):
        try:
            loop = asyncio.get_event_loop()
            status, resp_body = await loop.run_in_executor(None, _http_post)
            _last_flush_time = time.time()
            if 200 <= status < 300:
                _consecutive_failures = 0
                _last_flush_success = True
                return True
            if status in (401, 403):
                # Auth failure — not retryable
                log_for_debugging(
                    f"datadog: auth failure HTTP {status}, disabling"
                )
                _consecutive_failures = CIRCUIT_BREAKER_THRESHOLD
                _last_flush_success = False
                return False
            if status == 413:
                # Payload too large — not retryable, but split on next flush
                log_for_debugging("datadog: payload too large (413)")
                _last_flush_success = False
                return False
            if status >= 500:
                log_for_debugging(
                    f"datadog: server error HTTP {status} (attempt {attempt})"
                )
                _last_flush_success = False
            else:
                log_for_debugging(
                    f"datadog: HTTP {status} (attempt {attempt})"
                )
                _last_flush_success = False
        except urllib.error.HTTPError as e:
            log_for_debugging(
                f"datadog: HTTP error {e.code} (attempt {attempt})"
            )
            _last_flush_success = False
            _last_flush_time = time.time()
            if e.code in (401, 403):
                _consecutive_failures = CIRCUIT_BREAKER_THRESHOLD
                return False
        except (urllib.error.URLError, OSError) as e:
            log_for_debugging(
                f"datadog: connection error: {e} (attempt {attempt})"
            )
            _last_flush_success = False
            _last_flush_time = time.time()
        except Exception as e:
            log_for_debugging(
                f"datadog: unexpected error: {e} (attempt {attempt})"
            )
            _last_flush_success = False
            _last_flush_time = time.time()

        if attempt < MAX_FLUSH_RETRIES:
            delay = BASE_RETRY_DELAY * attempt
            await asyncio.sleep(delay)

    # All retries exhausted
    _consecutive_failures += 1
    _total_flush_errors += 1
    if _consecutive_failures >= CIRCUIT_BREAKER_THRESHOLD:
        _circuit_open_until = now + CIRCUIT_BREAKER_COOLDOWN
        log_for_debugging(
            f"datadog: circuit breaker tripped after {_consecutive_failures} failures"
        )
    return False


# ---------------------------------------------------------------------------
# Flush loop
# ---------------------------------------------------------------------------


def _dequeue_batch(max_series: int, max_points: int) -> list[MetricPoint]:
    """Dequeue up to *max_series* points (but not more than *max_points*
    total data-points) from the global queue."""
    batch: list[MetricPoint] = []
    point_count = 0
    while _metrics_queue and len(batch) < max_series and point_count < max_points:
        point = _metrics_queue.pop(0)
        batch.append(point)
        point_count += 1
    return batch


async def flush_datadog_metrics() -> int:
    """Flush all pending metrics to Datadog.

    Returns the number of metric points flushed.
    """
    global _total_metrics_sent, _total_metrics_dropped, _last_flush_time

    if not datadog_sink_enabled():
        return 0
    if not _metrics_queue:
        return 0

    total = 0
    async with _flush_lock:
        while _metrics_queue:
            batch = _dequeue_batch(MAX_SERIES_PER_FLUSH, MAX_POINTS_PER_FLUSH)
            if not batch:
                break
            # Aggregate identical metrics before sending to reduce API volume
            aggregated = _aggregate_metrics(batch)
            success = await _flush_to_datadog(aggregated)
            _last_flush_time = time.time()
            if success:
                _total_metrics_sent += len(aggregated)
                total += len(aggregated)
            else:
                # On failure, re-queue up to some limit to avoid unbounded growth
                if len(_metrics_queue) < 2000:
                    _metrics_queue[:0] = batch
                break
    return total


def _aggregate_metrics(points: list[MetricPoint]) -> list[MetricPoint]:
    """Combine identical metric points (same name + type + tags + timestamp).

    COUNT and RATE metrics are summed. GAUGE and DISTRIBUTION metrics
    keep the last value (typical for latest-value gauges).
    """
    if len(points) <= 1:
        return points

    groups: dict[tuple, MetricPoint] = {}
    for point in points:
        key = (
            point.metric,
            int(point.type),
            tuple(sorted(point.tags)),
            point.timestamp,
        )
        if key in groups:
            existing = groups[key]
            if point.type in (MetricType.COUNT, MetricType.RATE):
                existing.value += point.value
            else:
                # Gauge / Distribution: keep latest value
                existing.value = point.value
        else:
            groups[key] = MetricPoint(
                metric=point.metric,
                value=point.value,
                type=point.type,
                tags=list(point.tags),
                timestamp=point.timestamp,
            )
    return list(groups.values())


async def wait_for_drain(*, timeout: float = 10.0, poll_interval: float = 0.1) -> bool:
    """Block until the metric queue is fully drained.

    Args:
        timeout: Maximum seconds to wait before giving up.
        poll_interval: How often to check the queue.

    Returns:
        True if the queue was fully drained, False on timeout.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _metrics_queue:
            return True
        # Trigger an immediate flush to accelerate draining
        try:
            await flush_datadog_metrics()
        except Exception:
            pass
        await asyncio.sleep(poll_interval)
    return len(_metrics_queue) == 0


async def flush_and_report() -> dict[str, Any]:
    """Flush pending metrics and return a status report.

    Useful for health-check endpoints and debugging.
    """
    flushed = await flush_datadog_metrics()
    return {
        "flushed": flushed,
        "remaining": len(_metrics_queue),
        "total_sent": _total_metrics_sent,
        "total_dropped": _total_metrics_dropped,
        "total_errors": _total_flush_errors,
        "circuit_open": time.time() < _circuit_open_until,
        "last_flush_time": _last_flush_time,
        "last_flush_success": _last_flush_success,
    }


async def _flush_loop(flush_interval: float) -> None:
    """Background loop that periodically flushes metrics."""
    while True:
        await asyncio.sleep(flush_interval)
        try:
            await flush_datadog_metrics()
        except Exception as e:
            log_for_debugging(f"datadog: flush loop error: {e}")


async def shutdown_datadog() -> None:
    """Gracefully shut down the Datadog sink — flush pending metrics.

    Called from ``graceful_shutdown`` via ``_shutdown_datadog``.
    """
    global _flush_task, _initialized
    if _flush_task is not None:
        _flush_task.cancel()
        try:
            await _flush_task
        except asyncio.CancelledError:
            pass
        _flush_task = None
    try:
        flushed = await flush_datadog_metrics()
        if flushed > 0:
            log_for_debugging(f"datadog: flushed {flushed} metrics on shutdown")
    except Exception as e:
        log_for_debugging(f"datadog: shutdown flush error: {e}")
    _initialized = False


def init_datadog(
    *,
    flush_interval: Optional[float] = None,
    tags_provider: Optional[Callable[[], list[str]]] = None,
) -> None:
    """Initialize the Datadog metrics sink and start the background flush loop.

    Args:
        flush_interval: Seconds between automatic flushes (default from
            ``DD_FLUSH_INTERVAL_SECONDS`` env var, or 30s).
        tags_provider: Optional callable returning a list of ``key:value``
            tags appended to every metric.
    """
    global _initialized, _flush_task, _tags_provider
    if _initialized:
        return
    _initialized = True
    if tags_provider is not None:
        _tags_provider = tags_provider
    if not datadog_sink_enabled():
        return
    interval = flush_interval if flush_interval is not None else _get_flush_interval()
    try:
        loop = asyncio.get_running_loop()
        _flush_task = loop.create_task(_flush_loop(interval))
        log_for_debugging(
            f"datadog: background flush started (interval={interval}s)"
        )
    except RuntimeError:
        pass  # No event loop yet — will start on first async use


# ---------------------------------------------------------------------------
# Analytics sink integration
# ---------------------------------------------------------------------------


# Map of analytics event names → Datadog metric names
_EVENT_TO_METRIC: dict[str, tuple[str, MetricType]] = {
    "session_started": ("hare.session.started", MetricType.COUNT),
    "session_ended": ("hare.session.ended", MetricType.COUNT),
    "api_request": ("hare.api.request", MetricType.COUNT),
    "api_error": ("hare.api.error", MetricType.COUNT),
    "api_latency_ms": ("hare.api.latency_ms", MetricType.DISTRIBUTION),
    "tool_use": ("hare.tool.invocation", MetricType.COUNT),
    "tool_error": ("hare.tool.error", MetricType.COUNT),
    "file_operation": ("hare.file.operation", MetricType.COUNT),
    "permission_prompt": ("hare.permission.prompt", MetricType.COUNT),
    "feature_flag": ("hare.feature.flag", MetricType.GAUGE),
    "model_switch": ("hare.model.switch", MetricType.COUNT),
    "crash": ("hare.crash", MetricType.COUNT),
    "compaction": ("hare.compaction", MetricType.COUNT),
}


def create_datadog_analytics_sink() -> dict[str, Any]:
    """Create an analytics sink that routes events to Datadog metrics.

    Usage::

        sink = create_datadog_analytics_sink()
        from hare.services.analytics.event_logger import attach_analytics_sink
        attach_analytics_sink(sink)
    """

    def _log_event(name: str, metadata: Optional[dict[str, Any]] = None) -> None:
        if not datadog_sink_enabled():
            return
        meta = metadata or {}
        try:
            send_datadog_metric_sync(_metric_name(name), _extract_value(meta),
                                     tags=_extract_tags(name, meta),
                                     metric_type=_metric_type(name))
        except Exception:
            pass  # Never let analytics sink errors propagate

    async def _log_event_async(
        name: str, metadata: Optional[dict[str, Any]] = None
    ) -> None:
        if not datadog_sink_enabled():
            return
        meta = metadata or {}
        try:
            await send_datadog_metric(
                _metric_name(name), _extract_value(meta),
                tags=_extract_tags(name, meta),
                metric_type=_metric_type(name))
        except Exception:
            pass

    return {
        "log_event": _log_event,
        "log_event_async": _log_event_async,
    }


def _metric_name(event_name: str) -> str:
    """Map an analytics event name to a Datadog metric name."""
    mapping = _EVENT_TO_METRIC.get(event_name)
    if mapping is not None:
        return mapping[0]
    raw = f"hare.event.{event_name.replace(' ', '_').lower()}"
    return normalize_metric_name(raw)


def _metric_type(event_name: str) -> MetricType:
    """Map an analytics event name to a Datadog metric type."""
    mapping = _EVENT_TO_METRIC.get(event_name)
    if mapping is not None:
        return mapping[1]
    return MetricType.COUNT


def _extract_value(metadata: dict[str, Any]) -> float:
    """Extract a numeric value from event metadata.

    Looks for ``value``, ``count``, ``duration_ms``, or ``count_tokens``
    keys; defaults to 1 (for COUNT-type events).
    """
    for key in ("value", "count"):
        if key in metadata:
            try:
                return float(metadata[key])
            except (TypeError, ValueError):
                pass
    if "duration_ms" in metadata:
        try:
            return float(metadata["duration_ms"])
        except (TypeError, ValueError):
            pass
    if "count_tokens" in metadata:
        try:
            return float(metadata["count_tokens"])
        except (TypeError, ValueError):
            pass
    return 1.0


def _extract_tags(
    _event_name: str, metadata: dict[str, Any]
) -> Optional[dict[str, str]]:
    """Extract tags from event metadata (safely).

    Only string-valued keys are treated as tags; numeric/boolean fields are
    excluded to avoid noisy cardinality.
    """
    tags: dict[str, str] = {}
    for key, value in metadata.items():
        if key.startswith("_"):
            continue
        if isinstance(value, str):
            tags[key] = value
        elif isinstance(value, bool):
            tags[key] = str(value).lower()
    return tags if tags else None


# ---------------------------------------------------------------------------
# Shutdown hook for graceful_shutdown module
# ---------------------------------------------------------------------------


async def _shutdown_datadog() -> None:
    """Shutdown hook called by the graceful_shutdown module.

    Flushes any pending metrics before the process exits.
    """
    await shutdown_datadog()


# ---------------------------------------------------------------------------
# Queue inspection (for testing / debugging)
# ---------------------------------------------------------------------------


def queue_size() -> int:
    """Return the number of metric points waiting in the queue."""
    return len(_metrics_queue)


def get_stats() -> dict[str, Any]:
    """Return diagnostic information about the Datadog sink."""
    return {
        "enabled": datadog_sink_enabled(),
        "initialized": _initialized,
        "queue_size": len(_metrics_queue),
        "circuit_open": time.time() < _circuit_open_until,
        "consecutive_failures": _consecutive_failures,
        "endpoint": _get_dd_metrics_endpoint(),
        "api_key_configured": bool(_get_dd_api_key()),
        "client_token_configured": bool(get_datadog_client_token()),
        "has_tags_provider": _tags_provider is not None,
        "total_metrics_sent": _total_metrics_sent,
        "total_metrics_dropped": _total_metrics_dropped,
        "total_flush_errors": _total_flush_errors,
        "last_flush_time": _last_flush_time,
        "last_flush_success": _last_flush_success,
        "queue_capacity_pct": round(
            len(_metrics_queue) / max(1, MAX_QUEUE_SIZE) * 100, 1
        ),
    }


# ---------------------------------------------------------------------------
# DogStatsD UDP sender
# ---------------------------------------------------------------------------

# DogStatsD is a lightweight UDP protocol for submitting metrics to the
# Datadog Agent.  This sender is useful when the Datadog Agent is running
# locally (e.g., in containerized environments).
#
# Packet format (one metric per line, terminated with \n):
#   <NAME>:<VALUE>|<TYPE>|#<TAG1>:<VAL1>,<TAG2>:<VAL2>

_dogstatsd_socket: Optional[Any] = None  # socket.socket | None


def _get_dogstatsd_target() -> Optional[tuple[str, int]]:
    """Read the DogStatsD host/port from environment.

    Returns (host, port) tuple, or None if DogStatsD is not configured.
    """
    host = os.environ.get("DD_DOGSTATSD_HOST") or os.environ.get("DD_AGENT_HOST")
    if not host:
        return None
    port_str = os.environ.get("DD_DOGSTATSD_PORT", "8125")
    try:
        port = int(port_str)
    except (ValueError, TypeError):
        port = 8125
    return host, port


def _dogstatsd_type_char(metric_type: MetricType) -> str:
    """Map MetricType to DogStatsD type character."""
    _map = {
        MetricType.COUNT: "c",
        MetricType.GAUGE: "g",
        MetricType.RATE: "g",  # DogStatsD has no rate type; use gauge
        MetricType.DISTRIBUTION: "d",
        MetricType.UNSPECIFIED: "c",
    }
    return _map.get(metric_type, "c")


def _format_dogstatsd(point: MetricPoint) -> str:
    """Format a single metric point as a DogStatsD line."""
    name = point.metric.replace(" ", "_")
    type_char = _dogstatsd_type_char(point.type)
    parts = [f"{name}:{point.value}|{type_char}"]
    if point.tags:
        tag_str = ",".join(point.tags)
        parts.append(f"|#{tag_str}")
    return "".join(parts)


def _dogstatsd_send_batch(points: list[MetricPoint]) -> int:
    """Send a batch of metric points via DogStatsD UDP.

    Returns the number of points sent, or 0 on failure.
    """
    import socket as _socket

    target = _get_dogstatsd_target()
    if target is None:
        return 0

    host, port = target
    lines = [_format_dogstatsd(p) for p in points]
    payload = "\n".join(lines).encode("utf-8")

    global _dogstatsd_socket
    try:
        # Create socket lazily, recreate on error
        if _dogstatsd_socket is None:
            _dogstatsd_socket = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
            _dogstatsd_socket.setblocking(False)
            _dogstatsd_socket.settimeout(2.0)
        _dogstatsd_socket.sendto(payload, (host, port))
        return len(points)
    except (OSError, _socket.error) as e:
        log_for_debugging(f"datadog: dogstatsd send error: {e}")
        # Reset socket on error so it's recreated next time
        try:
            _dogstatsd_socket.close()
        except Exception:
            pass
        _dogstatsd_socket = None
        return 0
    except Exception as e:
        log_for_debugging(f"datadog: dogstatsd unexpected error: {e}")
        _dogstatsd_socket = None
        return 0


def dogstatsd_enabled() -> bool:
    """Check if DogStatsD is configured (DD_AGENT_HOST or DD_DOGSTATSD_HOST set)."""
    return _get_dogstatsd_target() is not None


async def _flush_via_dogstatsd(points: list[MetricPoint]) -> int:
    """Flush a batch of points via DogStatsD (non-blocking UDP).

    Returns the number of points submitted.
    """
    if not points:
        return 0
    loop = asyncio.get_event_loop()
    try:
        sent = await loop.run_in_executor(None, _dogstatsd_send_batch, points)
        return sent
    except Exception:
        return 0


def _close_dogstatsd_socket() -> None:
    """Close the DogStatsD socket (called at shutdown)."""
    global _dogstatsd_socket
    if _dogstatsd_socket is not None:
        try:
            _dogstatsd_socket.close()
        except Exception:
            pass
        _dogstatsd_socket = None


# ---------------------------------------------------------------------------
# Extended shutdown
# ---------------------------------------------------------------------------


async def shutdown_datadog_full(*, drain_timeout: float = 10.0) -> dict[str, Any]:
    """Full graceful shutdown: drain queue, close connections, report.

    Args:
        drain_timeout: Max seconds to wait for queue drain.

    Returns:
        Final status report.
    """
    global _initialized

    # Cancel background flush loop
    if _flush_task is not None:
        _flush_task.cancel()
        try:
            await _flush_task
        except asyncio.CancelledError:
            pass
        _flush_task = None

    # Drain remaining metrics
    drained = await wait_for_drain(timeout=drain_timeout)
    if not drained:
        log_for_debugging(
            f"datadog: shutdown drain incomplete — {len(_metrics_queue)} points remaining"
        )

    # Close DogStatsD socket if open
    _close_dogstatsd_socket()

    _initialized = False
    return flush_and_report()
