"""
JWT utilities for bridge token refresh scheduling.

Port of: src/bridge/jwtUtils.ts

Proactive token refresh scheduler with:
  - JWT expiry decoding (sk-ant-si- prefix stripping)
  - Generation-based stale detection
  - Failure counting with max 3 retries
  - Fallback refresh interval (30 min)
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from typing import Any, Callable, Optional

# Constants matching TS
TOKEN_REFRESH_BUFFER_MS = 5 * 60 * 1000  # 5 minutes
FALLBACK_REFRESH_INTERVAL_MS = 30 * 60 * 1000  # 30 minutes
MAX_REFRESH_FAILURES = 3
REFRESH_RETRY_DELAY_MS = 60_000  # 1 minute


def _format_duration(ms: int) -> str:
    if ms < 60_000:
        return f"{round(ms / 1000)}s"
    m = ms // 60_000
    s = round((ms % 60_000) / 1000)
    return f"{m}m {s}s" if s > 0 else f"{m}m"


def decode_jwt_payload(token: str) -> Optional[dict[str, Any]]:
    """Decode a JWT's payload segment without verifying signature.

    Strips 'sk-ant-si-' prefix if present.
    """
    jwt = token[len("sk-ant-si-") :] if token.startswith("sk-ant-si-") else token
    parts = jwt.split(".")
    if len(parts) != 3 or not parts[1]:
        return None
    try:
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        decoded = base64.urlsafe_b64decode(padded).decode("utf-8")
        result = json.loads(decoded)
        return result if isinstance(result, dict) else None
    except Exception:
        return None


def decode_jwt_expiry(token: str) -> Optional[float]:
    """Decode the 'exp' claim from a JWT. Returns Unix seconds or None."""
    payload = decode_jwt_payload(token)
    if payload and "exp" in payload and isinstance(payload["exp"], (int, float)):
        return float(payload["exp"])
    return None


class TokenRefreshScheduler:
    """Proactive token refresh scheduler.

    Schedules token refreshes before expiry. Handles:
    - JWT-based scheduling (via `schedule`)
    - expires_in-based scheduling (via `schedule_from_expires_in`)
    - Failure counting with retry
    - Generation-based stale detection for in-flight refreshes
    """

    def __init__(
        self,
        get_access_token: Callable[[], Any],
        on_refresh: Callable[[str, str], Any],
        label: str = "bridge",
        refresh_buffer_ms: int = TOKEN_REFRESH_BUFFER_MS,
    ) -> None:
        self._get_access_token = get_access_token
        self._on_refresh = on_refresh
        self._label = label
        self._refresh_buffer_ms = refresh_buffer_ms

        self._timers: dict[str, Optional[asyncio.Task[Any]]] = {}
        self._failure_counts: dict[str, int] = {}
        self._generations: dict[str, int] = {}

    def _next_generation(self, session_id: str) -> int:
        gen = self._generations.get(session_id, 0) + 1
        self._generations[session_id] = gen
        return gen

    def schedule(self, session_id: str, token: str) -> None:
        """Schedule a token refresh based on JWT expiry."""
        expiry = decode_jwt_expiry(token)
        if not expiry:
            return

        # Clear existing timer
        existing = self._timers.get(session_id)
        if existing and not existing.done():
            existing.cancel()

        gen = self._next_generation(session_id)
        delay_ms = expiry * 1000 - time.time() * 1000 - self._refresh_buffer_ms

        if delay_ms <= 0:
            asyncio.ensure_future(self._do_refresh(session_id, gen))
            return

        self._timers[session_id] = asyncio.ensure_future(
            self._delayed_refresh(session_id, gen, delay_ms)
        )

    def schedule_from_expires_in(
        self, session_id: str, expires_in_seconds: float
    ) -> None:
        """Schedule refresh from an explicit TTL (seconds until expiry)."""
        existing = self._timers.get(session_id)
        if existing and not existing.done():
            existing.cancel()

        gen = self._next_generation(session_id)
        delay_ms = max(
            expires_in_seconds * 1000 - self._refresh_buffer_ms,
            30_000,  # 30s floor
        )

        self._timers[session_id] = asyncio.ensure_future(
            self._delayed_refresh(session_id, gen, delay_ms)
        )

    async def _delayed_refresh(
        self, session_id: str, gen: int, delay_ms: float
    ) -> None:
        await asyncio.sleep(delay_ms / 1000)
        await self._do_refresh(session_id, gen)

    async def _do_refresh(self, session_id: str, gen: int) -> None:
        """Execute the token refresh."""
        if self._generations.get(session_id) != gen:
            return  # Stale — superseded by newer schedule

        try:
            oauth_token = (
                await self._get_access_token()
                if asyncio.iscoroutinefunction(self._get_access_token)
                else self._get_access_token()
            )
        except Exception:
            oauth_token = None

        if self._generations.get(session_id) != gen:
            return  # Stale after await

        if not oauth_token:
            failures = self._failure_counts.get(session_id, 0) + 1
            self._failure_counts[session_id] = failures
            if failures < MAX_REFRESH_FAILURES:
                self._timers[session_id] = asyncio.ensure_future(
                    self._delayed_refresh(session_id, gen, REFRESH_RETRY_DELAY_MS)
                )
            return

        self._failure_counts.pop(session_id, None)

        # Call the refresh callback
        if asyncio.iscoroutinefunction(self._on_refresh):
            await self._on_refresh(session_id, oauth_token)
        else:
            self._on_refresh(session_id, oauth_token)

        # Schedule follow-up refresh
        self._timers[session_id] = asyncio.ensure_future(
            self._delayed_refresh(session_id, gen, FALLBACK_REFRESH_INTERVAL_MS)
        )

    def cancel(self, session_id: str) -> None:
        """Cancel refresh for a session."""
        self._next_generation(session_id)
        timer = self._timers.pop(session_id, None)
        if timer and not timer.done():
            timer.cancel()
        self._failure_counts.pop(session_id, None)

    def cancel_all(self) -> None:
        """Cancel all pending refreshes."""
        for sid in list(self._generations.keys()):
            self._next_generation(sid)
        for timer in self._timers.values():
            if timer and not timer.done():
                timer.cancel()
        self._timers.clear()
        self._failure_counts.clear()


def create_token_refresh_scheduler(
    get_access_token: Any,
    on_refresh: Any,
    label: str = "bridge",
    refresh_buffer_ms: int = TOKEN_REFRESH_BUFFER_MS,
) -> TokenRefreshScheduler:
    """Create a token refresh scheduler (mirrors TS factory function)."""
    return TokenRefreshScheduler(
        get_access_token=get_access_token,
        on_refresh=on_refresh,
        label=label,
        refresh_buffer_ms=refresh_buffer_ms,
    )
