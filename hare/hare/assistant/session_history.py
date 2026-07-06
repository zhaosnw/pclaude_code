"""
Session history — paginated event retrieval from the sessions API.

Port of: src/assistant/sessionHistory.ts

Retrieves SDKMessage events from GET /v1/sessions/{sessionId}/events
with cursor-based pagination (anchor_to_latest for newest, before_id for older).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional

HISTORY_PAGE_SIZE = 100


# ---------------------------------------------------------------------------
# Types — matching TS exactly
# ---------------------------------------------------------------------------


@dataclass
class HistoryPage:
    """A page of session events in chronological order."""

    events: list[dict[str, Any]] = field(default_factory=list)
    first_id: Optional[str] = None  # oldest event ID → before_id cursor for next page
    has_more: bool = False  # true = older events exist


@dataclass
class HistoryAuthCtx:
    """Prepared auth context — build once, reuse across pages."""

    base_url: str = ""
    headers: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Auth context factory
# ---------------------------------------------------------------------------


async def create_history_auth_ctx(
    session_id: str,
    *,
    prepare_api_request: Any = None,
    get_oauth_config: Any = None,
    get_oauth_headers: Any = None,
) -> HistoryAuthCtx:
    """Prepare auth + headers + base URL once, reuse across pages.

    Uses dependency injection to avoid hard imports:
      - prepare_api_request() → { accessToken, orgUUID }
      - get_oauth_config() → { BASE_API_URL }
      - get_oauth_headers(token) → { Authorization, ... }
    """
    # Resolve base URL (from OAuth config or fallback)
    if get_oauth_config:
        oauth_cfg = get_oauth_config()
        base_api_url = oauth_cfg.get("BASE_API_URL", "https://api.claude.ai")
    else:
        base_api_url = "https://api.claude.ai"

    base_url = f"{base_api_url}/v1/sessions/{session_id}/events"

    # Build auth headers
    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "anthropic-beta": "ccr-byoc-2025-07-29",
    }

    if prepare_api_request:
        try:
            auth = await prepare_api_request()
            access_token = auth.get("accessToken", "")
            org_uuid = auth.get("orgUUID", "")
        except Exception:
            access_token = ""
            org_uuid = ""
    else:
        access_token = ""
        org_uuid = ""

    # Add OAuth Authorization header
    if access_token:
        if get_oauth_headers:
            oauth_headers = get_oauth_headers(access_token)
            headers.update(oauth_headers)
        else:
            headers["Authorization"] = f"Bearer {access_token}"

    # Add org UUID header
    if org_uuid:
        headers["x-organization-uuid"] = org_uuid

    return HistoryAuthCtx(base_url=base_url, headers=headers)


# ---------------------------------------------------------------------------
# Page fetcher (internal)
# ---------------------------------------------------------------------------


async def _fetch_page(
    ctx: HistoryAuthCtx,
    params: dict[str, Any],
    label: str = "sessionHistory",
    http_get: Any = None,
) -> Optional[HistoryPage]:
    """Fetch a single page of session events.

    Uses injected http_get for async HTTP; falls back to urllib sync.
    Logs debug on failure (matching TS logForDebugging).
    """
    if http_get:
        # Async path with injected client
        try:
            resp = await http_get(
                ctx.base_url, params=params, headers=ctx.headers, timeout=15
            )
        except Exception:
            _debug_log(f"[{label}] HTTP error")
            return None

        if not resp or resp.get("status") != 200:
            _debug_log(
                f"[{label}] HTTP {resp.get('status', 'error') if resp else 'error'}"
            )
            return None

        data = resp.get("data", {}) if isinstance(resp.get("data"), dict) else {}
    else:
        # Sync fallback with urllib
        import urllib.request
        import urllib.error

        try:
            query = "&".join(f"{k}={_url_encode(v)}" for k, v in params.items())
            url = f"{ctx.base_url}?{query}" if query else ctx.base_url
            req = urllib.request.Request(url, headers=ctx.headers)
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            _debug_log(f"[{label}] HTTP {e.code}")
            return None
        except Exception:
            _debug_log(f"[{label}] HTTP error")
            return None

    # Safety: verify events is a list (matching TS Array.isArray check)
    events = data.get("data", [])
    if not isinstance(events, list):
        events = []

    return HistoryPage(
        events=events,
        first_id=data.get("first_id"),
        has_more=data.get("has_more", False),
    )


# ---------------------------------------------------------------------------
# Public pagination functions
# ---------------------------------------------------------------------------


async def fetch_latest_events(
    ctx: HistoryAuthCtx,
    limit: int = HISTORY_PAGE_SIZE,
    http_get: Any = None,
) -> Optional[HistoryPage]:
    """Newest page: last `limit` events, chronological, via anchor_to_latest.

    has_more=true means older events exist.
    """
    return await _fetch_page(
        ctx,
        {"limit": limit, "anchor_to_latest": True},
        "fetchLatestEvents",
        http_get=http_get,
    )


async def fetch_older_events(
    ctx: HistoryAuthCtx,
    before_id: str,
    limit: int = HISTORY_PAGE_SIZE,
    http_get: Any = None,
) -> Optional[HistoryPage]:
    """Older page: events immediately before `before_id` cursor."""
    return await _fetch_page(
        ctx,
        {"limit": limit, "before_id": before_id},
        "fetchOlderEvents",
        http_get=http_get,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _debug_log(msg: str) -> None:
    """Log debug messages. Override via logging configuration."""
    import logging

    logging.getLogger("hare.assistant").debug(msg)


def _url_encode(value: Any) -> str:
    """URL-encode a value for query string."""
    import urllib.parse

    if isinstance(value, bool):
        return "true" if value else "false"
    return urllib.parse.quote(str(value))
