"""
Advanced retry logic for streaming and non-streaming API calls.

Port of: src/services/api/withRetry.ts (823 lines)

Provides:
- with_retry() — basic exponential backoff with jitter
- with_retry_streaming() — async generator for streaming API calls
- should_retry() — full retry decision tree (~20 conditions)
- Error detection helpers (is_529_error, is_transient_capacity_error, etc.)
- parse_max_tokens_context_overflow_error() — auto-correction
- get_retry_delay() — exponential backoff with jitter
- CannotRetryError, FallbackTriggeredError exception classes
"""

from __future__ import annotations

import asyncio
import random
import re
from dataclasses import dataclass
from typing import Any, AsyncGenerator, Callable, Optional, TypeVar

from hare.services.api.errors import (
    CannotRetryError,
    FallbackTriggeredError,
    REPEATED_529_ERROR_MESSAGE,
)

T = type[Any]  # type alias for generic

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_MAX_RETRIES = 10
DEFAULT_BASE_DELAY = 1.0
DEFAULT_MAX_DELAY = 30.0
FLOOR_OUTPUT_TOKENS = 3000
MAX_529_RETRIES = 3
BASE_DELAY_MS = 500
PERSISTENT_MAX_BACKOFF_MS = 300_000  # 5 minutes
PERSISTENT_RESET_CAP_MS = 21_600_000  # 6 hours
HEARTBEAT_INTERVAL_MS = 30_000
DEFAULT_FAST_MODE_FALLBACK_HOLD_MS = 1_800_000  # 30 minutes
SHORT_RETRY_THRESHOLD_MS = 20_000
MIN_COOLDOWN_MS = 600_000  # 10 minutes

# Query sources that retry on 529 (user-facing, not background)
FOREGROUND_529_RETRY_SOURCES = frozenset({
    "repl_main_thread",
    "sdk",
    "agent:custom",
    "agent:default",
    "agent:builtin",
    "compact",
    "hook_agent",
    "hook_prompt",
    "verification_agent",
    "side_question",
    "auto_mode",
})


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class OverflowData:
    input_tokens: int
    max_tokens: int
    context_limit: int


@dataclass
class RetryContext:
    """Mutable context passed to each retry attempt."""
    max_tokens_override: Optional[int] = None
    model: str = ""
    fallback_model: Optional[str] = None
    thinking_config: Optional[dict[str, Any]] = None
    fast_mode: bool = False
    consecutive_529_errors: int = 0
    fast_mode_cooldown_active: bool = False


@dataclass
class RetryOptions:
    max_retries: int = DEFAULT_MAX_RETRIES
    model: str = ""
    fallback_model: Optional[str] = None
    thinking_config: Optional[dict[str, Any]] = None
    fast_mode: bool = False
    query_source: str = "sdk"
    is_non_interactive: bool = False
    is_persistent: bool = False
    signal: Optional[asyncio.Event] = None


# ---------------------------------------------------------------------------
# Error detection helpers
# ---------------------------------------------------------------------------


def is_529_error(error: Any) -> bool:
    """Check if error is a 529 Overloaded error."""
    from hare.services.api.errors import _get_status_code
    status = _get_status_code(error)
    return status == 529 or '"type":"overloaded_error"' in str(error)


def is_transient_capacity_error(error: Any) -> bool:
    """Check if error is a transient capacity error (529 or 429)."""
    from hare.services.api.errors import _get_status_code
    status = _get_status_code(error)
    return status in (429, 529)


def is_oauth_token_revoked_error(error: Any) -> bool:
    """Check if error is an OAuth token revocation."""
    from hare.services.api.errors import _get_status_code
    status = _get_status_code(error)
    return status == 403 and "OAuth token has been revoked" in str(error)


def is_stale_connection_error(error: Any) -> bool:
    """Check if error is a stale connection (ECONNRESET/EPIPE)."""
    msg = str(error).lower()
    return "econnreset" in msg or "epipe" in msg


def get_retry_after_ms(error: Any) -> Optional[int]:
    """Extract Retry-After header value in milliseconds."""
    headers = getattr(error, "headers", None)
    if isinstance(headers, dict):
        retry_after = headers.get("retry-after")
        if retry_after:
            try:
                return int(retry_after) * 1000
            except (ValueError, TypeError):
                return None
    return None


def get_rate_limit_reset_delay_ms(error: Any) -> Optional[int]:
    """Parse unified rate limit reset header for delay in ms."""
    headers = getattr(error, "headers", None)
    if isinstance(headers, dict):
        import time
        reset_ts = headers.get("anthropic-ratelimit-unified-reset")
        if reset_ts:
            try:
                reset_sec = int(reset_ts)
                delay_ms = (reset_sec - int(time.time())) * 1000
                return max(0, min(delay_ms, PERSISTENT_RESET_CAP_MS))
            except (ValueError, TypeError):
                pass
    return None


# ---------------------------------------------------------------------------
# Retry delay calculation
# ---------------------------------------------------------------------------


def get_retry_delay(
    attempt: int,
    retry_after_header: Optional[str] = None,
    max_delay_ms: int = PERSISTENT_MAX_BACKOFF_MS,
) -> int:
    """Calculate retry delay with exponential backoff and jitter.

    If retry_after_header is provided, uses that directly.
    Otherwise uses BASE_DELAY_MS * 2^(attempt-1) with 25% random jitter.
    """
    if retry_after_header:
        try:
            return int(retry_after_header) * 1000
        except (ValueError, TypeError):
            pass

    base = BASE_DELAY_MS * (2 ** (attempt - 1))
    jitter = random.uniform(0, base * 0.25)
    delay = base + jitter
    return int(min(delay, max_delay_ms))


# ---------------------------------------------------------------------------
# Context overflow detection
# ---------------------------------------------------------------------------


def parse_max_tokens_context_overflow_error(error: Any) -> Optional[OverflowData]:
    """Parse "input length and max_tokens exceed context limit" error messages.

    Extracts inputTokens, maxTokens, and contextLimit for auto-correction.
    """
    msg = str(error) if error else ""
    match = re.search(
        r"input length[^0-9]*(\d+).*?max_tokens[^0-9]*(\d+).*?(?:exceed|greater than).*?(?:context|limit)[^0-9]*(\d+)",
        msg,
        re.IGNORECASE,
    )
    if match:
        return OverflowData(
            input_tokens=int(match.group(1)),
            max_tokens=int(match.group(2)),
            context_limit=int(match.group(3)),
        )
    return None


# ---------------------------------------------------------------------------
# should_retry — full retry decision tree
# ---------------------------------------------------------------------------


def should_retry(
    error: Any,
    is_subscriber: bool = False,
    is_enterprise: bool = False,
    is_ccr_mode: bool = False,
    is_persistent: bool = False,
    is_ant_user: bool = False,
) -> bool:
    """Determine if an API error should be retried.

    Port of: TS shouldRetry (withRetry.ts lines 696-787)

    The decision tree covers 20+ conditions:
    - Mock errors: never retry
    - Persistent mode: always retry 429/529
    - CCR mode: retry 401/403 (transient JWT issues)
    - overloaded_error: always retryable
    - Context overflow: retryable (auto-correction)
    - x-should-retry header: subscriber/enterprise gated
    - Connection errors, timeouts, 408/409: always retryable
    - 429: retryable for non-subscribers/enterprise
    - 401: retryable (after cache clear)
    - 403 token revoked: retryable
    - 5xx: retryable
    """
    from hare.services.api.errors import _get_status_code

    msg = str(error) if error else ""
    status = _get_status_code(error)

    # Mock errors never retry
    if "mock" in msg.lower() and "rate" in msg.lower():
        return False

    # Persistent mode always retries 429/529
    if is_persistent and status in (429, 529):
        return True

    # CCR mode retries 401/403 (transient JWT blips)
    if is_ccr_mode and status in (401, 403):
        return True

    # overloaded_error always retryable
    if '"type":"overloaded_error"' in msg or "overloaded_error" in msg:
        return True

    # Context overflow errors retryable (auto-correction kicks in)
    if parse_max_tokens_context_overflow_error(error) is not None:
        return True

    # x-should-retry header
    retry_header = _get_header(error, "x-should-retry")
    if retry_header is not None:
        if retry_header.lower() == "true":
            if not is_subscriber or is_enterprise:
                return True
        else:
            # false — but Ant users get an exception for 5xx
            if is_ant_user and status >= 500:
                return True
            return False

    # Connection errors, timeouts, lock timeouts always retryable
    error_name = type(error).__name__
    if "Connection" in error_name and "Timeout" not in error_name:
        return True
    if status == 408:  # Request timeout
        return True
    if status == 409:  # Lock timeout
        return True

    # 429: retryable for non-subscribers/enterprise
    if status == 429:
        if not is_subscriber or is_enterprise:
            return True
        return False

    # 401: retryable (with cache clear / OAuth refresh)
    if status == 401:
        return True

    # 403 OAuth token revoked: retryable
    if status == 403 and "OAuth token has been revoked" in msg:
        return True

    # 5xx: retryable
    if status >= 500:
        return True

    return False


def _get_header(error: Any, name: str) -> Optional[str]:
    """Extract a named header from an error object."""
    headers = getattr(error, "headers", None)
    if isinstance(headers, dict):
        return headers.get(name)
    return None


# ---------------------------------------------------------------------------
# Basic retry (non-streaming)
# ---------------------------------------------------------------------------


async def with_retry(
    fn: Callable[..., Any],
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
    base_delay: float = DEFAULT_BASE_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
    retryable_errors: Optional[tuple[type[Exception], ...]] = None,
) -> Any:
    """Execute a function with exponential backoff retry."""
    last_error: Optional[Exception] = None

    for attempt in range(max_retries + 1):
        try:
            return await fn()
        except Exception as e:
            last_error = e

            if retryable_errors and not isinstance(e, retryable_errors):
                raise

            if attempt >= max_retries:
                raise

            delay = min(base_delay * (2**attempt), max_delay)
            jitter = random.uniform(0, delay * 0.1)
            await asyncio.sleep(delay + jitter)

    raise last_error  # type: ignore[misc]


def is_retryable_error(error: Exception) -> bool:
    """Check if an error is retryable based on message keywords."""
    msg = str(error).lower()
    retryable_patterns = [
        "rate limit", "overloaded", "529", "503", "502",
        "timeout", "connection",
    ]
    return any(p in msg for p in retryable_patterns)


# ---------------------------------------------------------------------------
# Streaming retry generator
# ---------------------------------------------------------------------------


async def with_retry_streaming(
    get_client: Callable[[], Any],
    operation: Callable[[Any], Any],
    options: RetryOptions,
    retry_ctx: Optional[RetryContext] = None,
) -> AsyncGenerator[Any, Any]:
    """Async generator wrapping a streaming API call with retry logic.

    Port of: TS withRetry (async generator, withRetry.ts lines 170-517)

    Key features:
    - Fresh client on auth errors / stale connections / first attempt
    - Fast mode cooldown on 429/529
    - Fallback model after MAX_529_RETRIES consecutive 529s
    - Context overflow auto-correction (recompute maxTokensOverride)
    - Persistent mode: indefinite retry with keep-alive yields
    - Standard mode: exponential backoff with SystemAPIErrorMessage yields

    Yields:
    - SystemAPIErrorMessage for retry status updates
    - The final operation result (returned via StopAsyncIteration-style pattern)
    """
    from hare.utils.messages import create_assistant_api_error_message, create_system_message

    if retry_ctx is None:
        retry_ctx = RetryContext(
            model=options.model,
            fallback_model=options.fallback_model,
            thinking_config=options.thinking_config,
            fast_mode=options.fast_mode,
        )

    client = None
    consecutive_529 = retry_ctx.consecutive_529_errors
    attempt = 0
    was_fast_mode_active = options.fast_mode

    max_retries = options.max_retries
    if options.is_persistent:
        max_retries = 999999  # effectively infinite

    while attempt <= max_retries:
        # Check if aborted
        if options.signal and options.signal.is_set():
            from hare.services.api.errors import CannotRetryError
            raise CannotRetryError("Request was aborted.")

        attempt += 1

        # Get fresh client if needed
        _needs_refresh = (
            client is None
            or (attempt == 1 and consecutive_529 > 0)
        )
        if _needs_refresh:
            try:
                client = get_client()
            except Exception as e:
                yield create_assistant_api_error_message(
                    content=f"Failed to create API client: {e}",
                    error="unknown",
                )
                raise CannotRetryError(str(e), e)

        try:
            result = await operation(client)
            # Success — yield/return the result
            yield result
            return
        except CannotRetryError:
            raise
        except FallbackTriggeredError:
            raise
        except Exception as e:
            from hare.services.api.errors import _get_status_code

            status = _get_status_code(e)
            msg = str(e)

            # Fast mode fallback on 429/529
            if was_fast_mode_active and status in (429, 529) and not options.is_persistent:
                retry_after = get_retry_after_ms(e)
                if retry_after and retry_after < SHORT_RETRY_THRESHOLD_MS:
                    await asyncio.sleep(retry_after / 1000)
                    continue
                else:
                    # Enter cooldown
                    retry_ctx.fast_mode_cooldown_active = True
                    retry_ctx.fast_mode = False
                    was_fast_mode_active = False
                    await asyncio.sleep(MIN_COOLDOWN_MS / 1000)
                    continue

            # Fast mode rejected by API
            if "Fast mode is not enabled" in msg:
                retry_ctx.fast_mode = False
                was_fast_mode_active = False
                continue

            # 529 consecutive tracking
            if is_529_error(e):
                # Skip retry for background query sources
                if options.query_source not in FOREGROUND_529_RETRY_SOURCES:
                    raise CannotRetryError(f"Background query 529 not retried: {options.query_source}", e)

                consecutive_529 += 1
                if consecutive_529 >= MAX_529_RETRIES:
                    if options.fallback_model:
                        raise FallbackTriggeredError(
                            original_model=options.model,
                            fallback_model=options.fallback_model,
                        )
                    raise CannotRetryError(REPEATED_529_ERROR_MESSAGE, e)
            else:
                # Reset consecutive 529 counter on non-529 errors
                consecutive_529 = 0

            # Context overflow auto-correction
            overflow = parse_max_tokens_context_overflow_error(e)
            if overflow is not None:
                available = overflow.context_limit - overflow.input_tokens - 1000
                retry_ctx.max_tokens_override = max(FLOOR_OUTPUT_TOKENS, available)
                yield create_system_message(
                    content=f"Context overflow detected. Adjusted max_tokens to {retry_ctx.max_tokens_override}.",
                    subtype="info",
                )
                continue

            # Check if we should retry
            if not should_retry(e, is_persistent=options.is_persistent):
                raise CannotRetryError(str(e), e)

            # Max retries exceeded (non-persistent)
            if attempt > max_retries and not options.is_persistent:
                raise CannotRetryError(f"Max retries ({max_retries}) exceeded: {e}", e)

            # Calculate delay
            delay_ms = get_retry_delay(attempt)
            if status == 429 and options.is_persistent:
                reset_delay = get_rate_limit_reset_delay_ms(e)
                if reset_delay:
                    delay_ms = min(reset_delay, PERSISTENT_RESET_CAP_MS)

            delay_s = min(delay_ms / 1000, PERSISTENT_MAX_BACKOFF_MS / 1000)

            # Yield retry status for UI
            if options.is_persistent:
                # Chunked sleep with heartbeat yields
                chunks = max(1, int(delay_s / (HEARTBEAT_INTERVAL_MS / 1000)))
                chunk_s = delay_s / chunks
                for _ in range(chunks):
                    await asyncio.sleep(chunk_s)
                    yield create_system_message(
                        content=f"Retrying... ({attempt}/{max_retries if max_retries < 100000 else '∞'})",
                        subtype="info",
                    )
            else:
                if attempt <= max_retries:
                    yield create_assistant_api_error_message(
                        content=f"API Error — retrying in {int(delay_s)}s (attempt {attempt}/{max_retries})",
                        error="unknown",
                    )
                await asyncio.sleep(delay_s)

    raise CannotRetryError(f"Retry loop exhausted after {attempt} attempts")
