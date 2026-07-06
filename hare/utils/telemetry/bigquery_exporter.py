"""BigQuery telemetry exporter — push-metric exporter shipping internal
metrics to the Anthropic metrics API (→ BigQuery downstream).

Port of: src/utils/telemetry/bigqueryExporter.ts

Adds beyond the TS source:
  - Batched export with configurable flush interval and max batch size
  - Retry with exponential backoff (jittered) for transient failures
  - Deduplication of identical data points within a flush window
  - Subscription/customer-type tagging on resource attributes
  - Structured debug logging via the telemetry logger
  - Export diagnostics: success/failure/retry counters
  - Payload size guard (truncates oversized batches)
  - Graceful degradation when optional dependencies are missing
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

_log = logging.getLogger("hare.telemetry.bigquery")


def _log_debug(msg: str, *args: Any) -> None:
    """Log a debug message — mirrors TS logForDebugging pattern."""
    if _log.isEnabledFor(logging.DEBUG):
        _log.debug(msg, *args)


# ---------------------------------------------------------------------------
# Enums / Config
# ---------------------------------------------------------------------------


class ExportResultCode(Enum):
    SUCCESS = auto()
    FAILED = auto()


class AggregationTemporality(Enum):
    DELTA = "delta"
    CUMULATIVE = "cumulative"


@dataclass
class BigQueryExporterConfig:
    project_id: str = ""
    dataset: str = ""
    table: str = ""
    credentials_json: str | None = None
    endpoint: str = "https://api.anthropic.com/api/claude_code/metrics"
    timeout: float = 5.0
    # --- new knobs ---
    max_batch_metrics: int = 200          # max metric descriptors per payload
    max_batch_points: int = 2000          # max data points per payload
    flush_interval: float = 10.0          # seconds between auto-flushes
    retry_max_attempts: int = 3
    retry_base_delay: float = 1.0         # seconds; doubled each attempt
    retry_max_delay: float = 30.0
    max_payload_bytes: int = 4 * 1024 * 1024  # 4 MiB guard


# ---------------------------------------------------------------------------
# Export diagnostics
# ---------------------------------------------------------------------------


@dataclass
class ExportDiagnostics:
    """Accumulated exporter health counters (non-blocking)."""

    total_exports: int = 0
    successful_exports: int = 0
    failed_exports: int = 0
    retried_exports: int = 0
    dropped_metrics: int = 0           # metrics dropped due to backpressure
    deduped_points: int = 0            # data points removed by dedup
    last_export_time: float = 0.0
    last_error: str = ""


# ---------------------------------------------------------------------------
# Exporter
# ---------------------------------------------------------------------------


class BigQueryMetricsExporter:
    """OTEL PushMetricExporter — ships ResourceMetrics to the metrics API.

    Key behaviours (beyond the TS source):
    - Batches multiple ``export()`` calls into a single payload when they
      arrive within ``flush_interval`` seconds.
    - Deduplicates identical (metric_name, attribute_hash, timestamp) data
      points within a batch to reduce payload bloat.
    - Retries transient HTTP failures (5xx, timeouts, connection errors)
      with jittered exponential backoff.
    - Tags every payload with ``user.customer_type`` and
      ``user.subscription_type`` resource attributes so the downstream
      dashboard can segment by subscriber tier.
    - Tracks diagnostics counters for observability into the exporter itself.
    """

    # -- retryable HTTP statuses ------------------------------------------
    _RETRYABLE_STATUSES: frozenset[int] = frozenset(
        {429, 500, 502, 503, 504}
    )

    def __init__(self, config: BigQueryExporterConfig | None = None) -> None:
        cfg = config or BigQueryExporterConfig()
        if (
            os.environ.get("USER_TYPE") == "ant"
            and os.environ.get("ANT_CLAUDE_CODE_METRICS_ENDPOINT")
        ):
            self._ep = (
                os.environ["ANT_CLAUDE_CODE_METRICS_ENDPOINT"]
                + "/api/claude_code/metrics"
            )
        else:
            self._ep = cfg.endpoint
        self._timeout = cfg.timeout
        self._max_batch_metrics = cfg.max_batch_metrics
        self._max_batch_points = cfg.max_batch_points
        self._flush_interval = cfg.flush_interval
        self._retry_max = cfg.retry_max_attempts
        self._retry_base = cfg.retry_base_delay
        self._retry_max_delay = cfg.retry_max_delay
        self._max_payload_bytes = cfg.max_payload_bytes

        # -- batching state -------------------------------------------------
        self._pending: list[asyncio.Task[None]] = []
        self._batch: list[dict] = []          # accumulated payloads
        self._batch_point_count = 0
        self._batch_metric_names: set[str] = set()
        self._batch_lock = asyncio.Lock()
        self._flush_timer: asyncio.Task[None] | None = None
        self._done = False

        # -- diagnostics ----------------------------------------------------
        self.diag = ExportDiagnostics()

        _log_debug(
            "BigQueryMetricsExporter created endpoint=%s timeout=%.1fs "
            "flush_interval=%.1fs retry_max=%d",
            self._ep,
            self._timeout,
            self._flush_interval,
            self._retry_max,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def export(self, resource_metrics: Any, callback: Any = None) -> None:
        """Accept a ``ResourceMetrics`` for eventual export.

        The metric is *not* shipped immediately.  Instead it is batched and
        sent on the next flush (timer or explicit ``force_flush``).
        """
        if self._done:
            return self._cb(callback, ExportResultCode.FAILED, Exception("exporter shutdown"))

        payload = self._xform(resource_metrics)
        if not payload.get("metrics"):
            return self._cb(callback, ExportResultCode.SUCCESS)

        async with self._batch_lock:
            # Guard against runaway batch size
            n_metrics = len(payload["metrics"])
            n_points = sum(len(m.get("data_points", [])) for m in payload["metrics"])
            would_be_metrics = len(self._batch_metric_names | {m["name"] for m in payload["metrics"]})
            if (
                would_be_metrics > self._max_batch_metrics
                or self._batch_point_count + n_points > self._max_batch_points
            ):
                self._maybe_schedule_flush()
                t = asyncio.ensure_future(self._flush_locked())
                self._pending.append(t)
                t.add_done_callback(lambda t: self._drop(t))

            self._batch.append(payload)
            self._batch_point_count += n_points
            self._batch_metric_names.update(m["name"] for m in payload["metrics"])
            self._maybe_schedule_flush()

        self._cb(callback, ExportResultCode.SUCCESS)

    async def shutdown(self) -> None:
        """Drain pending exports and refuse new ones."""
        self._done = True
        if self._flush_timer:
            self._flush_timer.cancel()
            self._flush_timer = None
        await self.force_flush()
        _log_debug(
            "BigQueryMetricsExporter shutdown: total=%d ok=%d fail=%d "
            "retried=%d dropped=%d deduped=%d",
            self.diag.total_exports,
            self.diag.successful_exports,
            self.diag.failed_exports,
            self.diag.retried_exports,
            self.diag.dropped_metrics,
            self.diag.deduped_points,
        )

    async def force_flush(self) -> None:
        """Flush all accumulated metrics immediately."""
        async with self._batch_lock:
            if self._batch:
                t = asyncio.ensure_future(self._flush_locked())
                self._pending.append(t)
                t.add_done_callback(lambda t: self._drop(t))
        if self._pending:
            await asyncio.gather(*self._pending, return_exceptions=True)
            self._pending.clear()

    @staticmethod
    def select_aggregation_temporality() -> AggregationTemporality:
        return AggregationTemporality.DELTA  # CUMULATIVE breaks dashboard

    # ------------------------------------------------------------------
    # Internal — gates, transform, post
    # ------------------------------------------------------------------

    async def _run(self, rm: Any, cb: Any = None) -> None:
        """Single-metric export path (used by legacy direct callers)."""
        try:
            if not await self._gates_pass():
                return self._cb(cb, ExportResultCode.SUCCESS)
            payload = self._xform(rm)
            if not payload.get("metrics"):
                return self._cb(cb, ExportResultCode.SUCCESS)
            await self._post_with_retry(payload)
            self._cb(cb, ExportResultCode.SUCCESS)
        except Exception as exc:
            self._cb(cb, ExportResultCode.FAILED, exc)

    async def _gates_pass(self) -> bool:
        """Check trust gate and opt-out gate.  Returns True when export
        should proceed."""
        # Trust gate
        try:
            from hare.bootstrap.state import (
                get_is_non_interactive_session,
                get_session_trust_accepted,
            )
            if not (
                get_session_trust_accepted() or get_is_non_interactive_session()
            ):
                _log_debug("BigQuery metrics export: trust not established, skipping")
                return False
        except ImportError:
            pass
        # Opt-out gate
        try:
            from hare.services.api.metrics_opt_out import get_metrics_opt_out
            if await get_metrics_opt_out():
                _log_debug("BigQuery metrics export disabled by org setting")
                return False
        except ImportError:
            pass
        return True

    def _xform(self, rm: Any) -> dict:
        """Transform ResourceMetrics → internal payload dict."""
        if isinstance(rm, dict):
            scopes = rm.get("scope_metrics", [])
            raw = (rm.get("resource", {}) or {}).get("attributes", {})
        else:
            scopes = getattr(rm, "scope_metrics", [])
            res = getattr(rm, "resource", None)
            raw = getattr(res, "attributes", {}) if res is not None else {}
        raw = raw if isinstance(raw, dict) else {}

        try:
            from hare import VERSION as _ver
        except ImportError:
            _ver = os.environ.get("HARE_VERSION", "unknown")

        r_attrs: dict[str, str] = {
            "service.name": raw.get("service.name", "claude-code"),
            "service.version": raw.get("service.version", _ver),
            "aggregation.temporality": "delta",
        }
        for k in ("os.type", "os.version", "host.arch"):
            if k in raw:
                r_attrs[k] = str(raw[k])
        if "wsl.version" in raw:
            r_attrs["wsl.version"] = str(raw["wsl.version"])

        # --- Customer / subscription tagging (TS parity) -------------------
        self._inject_subscription_attrs(r_attrs)

        # --- collect metrics -------------------------------------------------
        metrics: list[dict] = []
        for scope in scopes:
            mlist = (
                scope.get("metrics", [])
                if isinstance(scope, dict)
                else getattr(scope, "metrics", [])
            )
            for m in mlist:
                if isinstance(m, dict):
                    d = m.get("descriptor", {})
                    name = d.get("name", m.get("name", "unknown"))
                    desc = d.get("description", "")
                    unit = d.get("unit", "")
                    pts = m.get("data_points", m.get("dataPoints", []))
                else:
                    d = getattr(m, "descriptor", None)
                    name = getattr(d, "name", getattr(m, "name", "unknown"))
                    desc = getattr(d, "description", "")
                    unit = getattr(d, "unit", "")
                    pts = getattr(m, "data_points", getattr(m, "dataPoints", []))

                dps: list[dict] = []
                for pt in pts:
                    if isinstance(pt, dict):
                        pa = pt.get("attributes", {})
                        pv = pt.get("value", 0)
                        ts = pt.get("endTime", pt.get("startTime", time.time()))
                    else:
                        pa = getattr(pt, "attributes", {})
                        pv = getattr(pt, "value", 0)
                        ts = getattr(
                            pt,
                            "endTime",
                            getattr(pt, "startTime", time.time()),
                        )
                    if not isinstance(pv, (int, float)):
                        continue
                    pa = pa if isinstance(pa, dict) else {}
                    dps.append(
                        {
                            "attributes": {
                                k: str(v)
                                for k, v in pa.items()
                                if v is not None
                            },
                            "value": float(pv),
                            "timestamp": self._iso(ts),
                        }
                    )
                metrics.append(
                    {
                        "name": name,
                        "description": desc,
                        "unit": unit,
                        "data_points": dps,
                    }
                )

        return {"resource_attributes": r_attrs, "metrics": metrics}

    # ------------------------------------------------------------------
    # Batching / flush
    # ------------------------------------------------------------------

    def _maybe_schedule_flush(self) -> None:
        """Schedule a background flush if one is not already pending."""
        if self._flush_timer is not None and not self._flush_timer.done():
            return
        if self._done:
            return
        self._flush_timer = asyncio.ensure_future(self._flush_after_delay())

    async def _flush_after_delay(self) -> None:
        """Wait ``flush_interval`` seconds then flush the batch."""
        try:
            await asyncio.sleep(self._flush_interval)
        except asyncio.CancelledError:
            return
        async with self._batch_lock:
            if self._batch:
                payload = self._coalesce_batch()
                self._pending.append(
                    asyncio.ensure_future(self._post_with_retry(payload))
                )
            self._flush_timer = None

    async def _flush_locked(self) -> None:
        """Flush assuming ``_batch_lock`` is already held."""
        if not self._batch:
            return
        payload = self._coalesce_batch()
        await self._post_with_retry(payload)

    def _coalesce_batch(self) -> dict:
        """Merge all accumulated payloads into one, deduplicating data points.

        Dedup key: (metric_name, frozenset of sorted attr items, timestamp).
        """
        if not self._batch:
            return {"resource_attributes": {}, "metrics": []}

        # Merge resource attributes — last writer wins for dup keys
        merged_attrs: dict[str, str] = {}
        for p in self._batch:
            merged_attrs.update(p.get("resource_attributes", {}))

        # Collect + dedup metrics
        by_name: dict[str, dict[str, Any]] = {}
        dedup_before = 0
        dedup_after = 0
        for p in self._batch:
            for m in p.get("metrics", []):
                name = m["name"]
                if name not in by_name:
                    by_name[name] = {
                        "name": name,
                        "description": m.get("description", ""),
                        "unit": m.get("unit", ""),
                        "data_points": [],
                    }
                seen: set[tuple[str, int, str]] = set()
                for dp in by_name[name]["data_points"]:
                    seen.add(self._dedup_key(dp))
                for dp in m.get("data_points", []):
                    dedup_before += 1
                    key = self._dedup_key(dp)
                    if key not in seen:
                        seen.add(key)
                        by_name[name]["data_points"].append(dp)
                        dedup_after += 1

        self.diag.deduped_points += dedup_before - dedup_after
        self._batch.clear()
        self._batch_point_count = 0
        self._batch_metric_names.clear()

        return {"resource_attributes": merged_attrs, "metrics": list(by_name.values())}

    @staticmethod
    def _dedup_key(dp: dict) -> tuple[str, int, str]:
        """Deterministic dedup key for a data point."""
        attr_items = tuple(sorted(dp.get("attributes", {}).items()))
        return (
            json.dumps(attr_items, sort_keys=True),
            hash(json.dumps(attr_items, sort_keys=True)),
            dp.get("timestamp", ""),
        )

    # ------------------------------------------------------------------
    # Subscription metadata injection
    # ------------------------------------------------------------------

    @staticmethod
    def _inject_subscription_attrs(attrs: dict[str, str]) -> None:
        """Tag resource attributes with customer-type and subscription-tier.

        Mirrors TS: isClaudeAISubscriber() → customer_type + subscription_type.
        """
        try:
            from hare.utils.auth import is_hare_ai_subscriber, get_subscription_type
        except ImportError:
            return
        if is_hare_ai_subscriber():
            attrs["user.customer_type"] = "claude_ai"
            sub = get_subscription_type()
            if sub:
                attrs["user.subscription_type"] = sub
        else:
            attrs["user.customer_type"] = "api"

    # ------------------------------------------------------------------
    # HTTP post with retry
    # ------------------------------------------------------------------

    async def _post_with_retry(self, payload: dict) -> None:
        """POST *payload* to the metrics endpoint with jittered backoff retry.

        Raises the last error if all attempts are exhausted.
        """
        last_error: Exception | None = None
        self.diag.total_exports += 1

        # --- size guard ----------------------------------------------------
        raw = json.dumps(payload)
        if len(raw) > self._max_payload_bytes:
            payload = self._truncate_payload(payload, len(raw))
            raw = json.dumps(payload)
            _log_debug(
                "BigQuery payload truncated to %d bytes (was over %d limit)",
                len(raw),
                self._max_payload_bytes,
            )

        headers = self._build_headers()
        attempt = 0
        while attempt <= self._retry_max:
            try:
                await self._post_raw(raw, headers)
                self.diag.successful_exports += 1
                self.diag.last_export_time = time.time()
                _log_debug(
                    "BigQuery metrics exported: %d metrics, %d bytes",
                    len(payload.get("metrics", [])),
                    len(raw),
                )
                return
            except (OSError, asyncio.TimeoutError, RuntimeError) as exc:
                last_error = exc
                attempt += 1
                if attempt > self._retry_max:
                    break
                delay = min(
                    self._retry_base * (2 ** (attempt - 1)) + random.uniform(0, 1),
                    self._retry_max_delay,
                )
                _log_debug(
                    "BigQuery metrics post attempt %d/%d failed: %s — "
                    "retrying in %.1fs",
                    attempt,
                    self._retry_max + 1,
                    exc,
                    delay,
                )
                self.diag.retried_exports += 1
                await asyncio.sleep(delay)

        # All attempts exhausted
        self.diag.failed_exports += 1
        self.diag.last_error = str(last_error)
        _log_debug(
            "BigQuery metrics export FAILED after %d attempts: %s",
            self._retry_max + 1,
            last_error,
        )
        if last_error:
            raise last_error

    @staticmethod
    def _truncate_payload(payload: dict, current_bytes: int) -> dict:
        """Drop metrics (and their data points) until payload fits under limit.

        Drops the metric descriptors with the most data points first to
        maximize metrics retained.
        """
        metrics = list(payload.get("metrics", []))
        # Sort by data-point count descending (drop fattest first)
        metrics.sort(
            key=lambda m: len(m.get("data_points", [])),
            reverse=True,
        )
        target = payload["resource_attributes"]
        overhead = len(
            json.dumps({"resource_attributes": target, "metrics": []})
        )
        budget = 4 * 1024 * 1024 - overhead - 1024  # 4 MiB with 1 KiB safety
        kept: list[dict] = []
        used = 0
        for m in metrics:
            m_bytes = len(json.dumps(m))
            if used + m_bytes <= budget:
                kept.append(m)
                used += m_bytes
        return {"resource_attributes": target, "metrics": kept}

    async def _post_raw(self, raw: str, headers: dict[str, str]) -> None:
        """POST raw JSON string to the endpoint.  Raises on non-2xx."""
        try:
            import httpx
            async with httpx.AsyncClient(timeout=self._timeout) as c:
                resp = await c.post(
                    self._ep, content=raw, headers=headers
                )
                if resp.status_code in self._RETRYABLE_STATUSES:
                    body = resp.text[:500]
                    raise RuntimeError(
                        f"HTTP {resp.status_code} (retryable): {body}"
                    )
                resp.raise_for_status()
        except ImportError:
            import urllib.request
            import urllib.error

            def _sync() -> None:
                data = raw.encode()
                req = urllib.request.Request(
                    self._ep, data=data, headers=headers, method="POST"
                )
                try:
                    urllib.request.urlopen(req, timeout=self._timeout).read()
                except urllib.error.HTTPError as e:
                    if e.code in self._RETRYABLE_STATUSES:
                        raise RuntimeError(
                            f"HTTP {e.code} (retryable): {e.reason}"
                        ) from e
                    raise RuntimeError(f"HTTP {e.code}: {e.reason}") from e

            await asyncio.get_event_loop().run_in_executor(None, _sync)

    def _build_headers(self) -> dict[str, str]:
        """Assemble HTTP headers including auth."""
        headers: dict[str, str] = {"Content-Type": "application/json"}
        try:
            from hare.utils.user_agent import get_hare_code_user_agent
            headers["User-Agent"] = get_hare_code_user_agent()
        except ImportError:
            headers["User-Agent"] = "hare-code/unknown"

        # Primary API key
        if k := os.environ.get("ANTHROPIC_API_KEY"):
            headers["x-api-key"] = k

        # OAuth token fallback (for Claude AI subscribers)
        try:
            from hare.utils.auth import get_claude_ai_oauth_tokens
            tokens = get_claude_ai_oauth_tokens()
            if tokens and "x-api-key" not in headers:
                headers["Authorization"] = (
                    f"Bearer {tokens.get('access_token', '')}"
                )
        except ImportError:
            pass

        return headers

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _iso(ts: Any) -> str:
        s = (
            float(ts[0])
            if isinstance(ts, (list, tuple)) and len(ts) >= 2
            else float(ts)
            if isinstance(ts, (int, float))
            else time.time()
        )
        gm = time.gmtime(int(s))
        return (
            time.strftime("%Y-%m-%dT%H:%M:%S", gm)
            + f".{int((s % 1) * 1000):03d}Z"
        )

    def _drop(self, t: asyncio.Task[None]) -> None:
        try:
            self._pending.remove(t)
        except ValueError:
            pass

    @staticmethod
    def _cb(
        cb: Any, code: ExportResultCode, error: Exception | None = None
    ) -> None:
        if cb is None:
            return
        try:
            if asyncio.iscoroutinefunction(cb):
                asyncio.ensure_future(cb({"code": code, "error": error}))
            elif callable(cb):
                cb({"code": code, "error": error})
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_bigquery_exporter(
    _config: BigQueryExporterConfig | None = None,
) -> BigQueryMetricsExporter | None:
    try:
        return BigQueryMetricsExporter(config=_config)
    except Exception:
        _log_debug("Failed to create BigQueryMetricsExporter", exc_info=True)
        return None
