"""
Validate model strings against allowlist and optional API probe.

Port of: src/utils/model/validateModel.ts — expanded with:
- Class-specific SDK error classification (NotFound, Auth, Connection, RateLimit, API)
- get_3p_fallback_suggestion for 3rd-party provider fallback hints
- Deprecated model detection
- is_valid_model / validate_models convenience functions
- Cache with TTL, max-size eviction, atomic clear
- Model string normalisation before the side-query probe
- Timeout support for the side query
- ValidationResult dataclass for structured return
- Comprehensive error body inspection
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import weakref
from dataclasses import dataclass, field
from typing import Any

from hare.utils.model.aliases import (
    MODEL_ALIASES,
    is_model_alias,
    is_model_family_alias,
)
from hare.utils.model.deprecation import get_deprecation_message, is_model_deprecated
from hare.utils.model.model_allowlist import is_model_allowed
from hare.utils.model.model_full import get_best_model, get_default_sonnet_model
from hare.utils.model.model_strings import get_model_strings
from hare.utils.model.providers import get_api_provider

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Default validation timeout (seconds) — the side query should be tiny.
_VALIDATION_TIMEOUT_S = 5.0

# Maximum cache entries before eviction kicks in.
_MAX_CACHE_SIZE = 512

# Time-to-live for a positive cache entry (seconds).  Errors are NOT cached
# long-term so the user can retry after fixing credentials, etc.
_CACHE_TTL_S = 900  # 15 minutes

# These HTTP statuses are retryable (transient) for model validation but we
# treat them as non-fatal here — the caller can retry.
_RETRYABLE_STATUSES: frozenset[int] = frozenset({429, 503, 502, 504})


# ---------------------------------------------------------------------------
# Custom validation exceptions (for structured error handling)
# ---------------------------------------------------------------------------


class ModelNotFoundError(Exception):
    """Raised when the API responds with a 404 / not_found_error for a model."""

    def __init__(self, model: str, message: str = "") -> None:
        self.model = model
        super().__init__(message or f"Model {model!r} not found")


class ModelAuthenticationError(Exception):
    """Raised when API credentials are missing or invalid."""

    def __init__(self, message: str = "Authentication failed") -> None:
        super().__init__(message)


class ModelConnectionError(Exception):
    """Raised when a network / connection error occurs."""

    def __init__(self, message: str = "Network error") -> None:
        super().__init__(message)


class ModelRateLimitError(Exception):
    """Raised when the API returns a 429 rate-limit response."""

    def __init__(self, message: str = "Rate limited", retry_after: float | None = None) -> None:
        self.retry_after = retry_after
        super().__init__(message)


class ModelAPIError(Exception):
    """Raised for generic API-level errors (non-404, non-401, non-429)."""

    def __init__(self, status: int | None, message: str) -> None:
        self.status = status
        super().__init__(message)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class ValidationResult:
    """Structured result from a model-validation call.

    Fields:
        valid:   Whether the model passed all checks.
        error:   Human-readable error message when ``valid`` is ``False``.
        deprecated: Whether the model is deprecated (but still usable).
        deprecation_message: Warning string if the model is deprecated.
        fallback: Suggested fallback model name, or ``None``.
    """

    valid: bool
    error: str = ""
    deprecated: bool = False
    deprecation_message: str = ""
    fallback: str | None = None


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

_CacheEntry = tuple[float, bool]  # (expiry_ts, is_valid)


_valid_model_cache: dict[str, _CacheEntry] = {}
_cache_lock: Any = None  # lazily created asyncio.Lock


def _get_cache_lock() -> Any:
    """Return or create the module-level asyncio lock for cache access."""
    global _cache_lock
    if _cache_lock is None:
        _cache_lock = asyncio.Lock()
    return _cache_lock


def _cache_get(model: str) -> bool | None:
    """Return cached validity for *model*, or ``None`` if not present / expired."""
    entry = _valid_model_cache.get(model)
    if entry is None:
        return None
    expiry, valid = entry
    if time.monotonic() > expiry:
        del _valid_model_cache[model]
        return None
    return valid


def _cache_set(model: str, valid: bool, ttl_s: float | None = None) -> None:
    """Store *valid* in the cache with an optional custom TTL.

    Enforces a maximum cache size by evicting the oldest entries first.
    """
    ttl = ttl_s if ttl_s is not None else _CACHE_TTL_S
    _valid_model_cache[model] = (time.monotonic() + ttl, valid)

    # Evict oldest entries if over capacity
    while len(_valid_model_cache) > _MAX_CACHE_SIZE:
        oldest = min(_valid_model_cache, key=lambda k: _valid_model_cache[k][0])
        del _valid_model_cache[oldest]


def _cache_remove(model: str) -> None:
    """Remove a single model from the cache."""
    _valid_model_cache.pop(model, None)


def clear_validation_cache() -> None:
    """Clear all cached validation results (public entry point)."""
    _valid_model_cache.clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize_model_for_api(model: str) -> str:
    """Strip the ``[1m]`` suffix (case-insensitive) before API calls.

    The ``[1m]`` tag is a CLAUDE_CODE-internal marker that the API does not
    understand, so we remove it for the probe while keeping it on the
    original model string for other bookkeeping.
    """
    normalized = model.strip()
    if normalized.lower().endswith("[1m]"):
        return normalized[:-4].strip()
    return normalized


def _is_alias_or_custom(model: str) -> bool:
    """Return ``True`` for known aliases and user-configured custom model env-var.

    These are pre-validated and do **not** need an API probe.
    """
    if is_model_alias(model) or is_model_family_alias(model):
        return True
    custom = os.environ.get("ANTHROPIC_CUSTOM_MODEL_OPTION")
    if custom and model == custom:
        return True
    return False


def _classify_api_error(
    error: BaseException,
    response_body: dict[str, Any] | None = None,
) -> Exception:
    """Map a raw exception from the side query to a typed validation exception.

    Parameters:
        error: The exception caught during the API call.
        response_body: Optional parsed JSON body from a failed HTTP response.

    Returns:
        A more specific ``Model*Error`` subclass, or the original
        ``error`` unchanged if classification is not possible.
    """
    # --- Inspect response body for structured error information ---
    if response_body and isinstance(response_body, dict):
        err_type = response_body.get("type", "")
        err_msg = response_body.get("error", {}).get("message", "")
        if not err_msg:
            err_msg = response_body.get("message", "")

        # 401 / authentication
        if err_type in ("authentication_error",) or response_body.get("status") == 401:
            return ModelAuthenticationError(
                err_msg or "Authentication failed. Please check your API credentials."
            )

        # 429 / rate limit
        if err_type in ("rate_limit_error",) or response_body.get("status") == 429:
            return ModelRateLimitError(
                err_msg or "Rate limited. Please wait and try again.",
            )

        # Not found
        if err_type == "not_found_error" or response_body.get("status") == 404:
            return ModelNotFoundError(
                model="unknown",  # caller fills in
                message=err_msg or "Model not found",
            )

        # Overloaded
        if err_type in ("overloaded_error",) or response_body.get("status") in (529,):
            return ModelAPIError(
                status=response_body.get("status"),
                message=err_msg or "Service is currently overloaded. Please try again later.",
            )

    # --- Heuristic based on exception type name / attributes ---
    cls_name = type(error).__name__

    # HTTP status code attached to the error
    status = getattr(error, "status_code", None) or getattr(error, "status", None)
    if status is not None:
        if status == 401:
            return ModelAuthenticationError(str(error))
        if status == 404:
            return ModelNotFoundError(model="unknown", message=str(error))
        if status == 429:
            return ModelRateLimitError(str(error))
        if status in (503, 502, 504):
            return ModelConnectionError(
                f"Service temporarily unavailable (HTTP {status}). "
                "Please try again later."
            )

    # --- Name-based classification (catches common SDK / requests pattern) ---
    lower_name = cls_name.lower()
    lower_msg = str(error).lower()

    if any(kw in lower_name for kw in ("notfound", "not_found", "404")):
        return ModelNotFoundError(model="unknown", message=str(error))
    if any(kw in lower_name for kw in ("auth", "unauthorized", "forbidden")):
        return ModelAuthenticationError(str(error))
    if any(kw in lower_name for kw in ("connection", "connect", "network", "timeout")):
        return ModelConnectionError(str(error))
    if any(kw in lower_name for kw in ("ratelimit", "rate_limit")):
        return ModelRateLimitError(str(error))

    # Fallback message inspection
    if "not found" in lower_msg or "404" in lower_msg:
        return ModelNotFoundError(model="unknown", message=str(error))
    if "unauthorized" in lower_msg or "forbidden" in lower_msg:
        return ModelAuthenticationError(str(error))
    if "connection" in lower_msg or "network" in lower_msg:
        return ModelConnectionError(str(error))
    if "rate limit" in lower_msg or "too many requests" in lower_msg:
        return ModelRateLimitError(str(error))

    # Generic API error
    if hasattr(error, "message"):
        return ModelAPIError(status=status, message=error.message)
    return ModelAPIError(status=status, message=str(error))


# ---------------------------------------------------------------------------
# 3rd-party fallback suggestions
# ---------------------------------------------------------------------------


def _get_3p_fallback_suggestion(model: str) -> str | None:
    """Suggest a fallback model for 3P users when the requested model is unavailable.

    Only active for non-first-party providers (Bedrock, Vertex, Foundry).

    @[MODEL LAUNCH]: Add fallback chains for new → previous version here.
    """
    provider = get_api_provider()
    if provider == "firstParty":
        return None

    lower = model.lower()

    # Opus 4.6 → Opus 4.1
    if "opus-4-6" in lower or "opus_4_6" in lower:
        return get_model_strings().get("opus41", "claude-opus-4-1-20250805")

    # Sonnet 4.6 → Sonnet 4.5
    if "sonnet-4-6" in lower or "sonnet_4_6" in lower:
        return get_model_strings().get("sonnet45", "claude-sonnet-4-5-20250929")

    # Sonnet 4.5 → Sonnet 4
    if "sonnet-4-5" in lower or "sonnet_4_5" in lower:
        return get_model_strings().get("sonnet40", "claude-sonnet-4-20250514")

    # Opus 4.5 → Opus 4.1
    if "opus-4-5" in lower or "opus_4_5" in lower:
        return get_model_strings().get("opus41", "claude-opus-4-1-20250805")

    # Sonnet 4 → Sonnet 3.7
    if "sonnet-4" in lower or "sonnet_4" in lower:
        return get_model_strings().get("sonnet37", "hare-3-7-sonnet-20250219")

    # Haiku 4.5 → Haiku 3.5
    if "haiku-4-5" in lower or "haiku_4_5" in lower:
        return get_model_strings().get("haiku35", "hare-3-5-haiku-20241022")

    return None


# ---------------------------------------------------------------------------
# Side query (API probe)
# ---------------------------------------------------------------------------

# Module-level reference to a side-query callable so callers can inject
# their own implementation without monkey-patching the module.
_side_query_fn: Any = None


def set_side_query(fn: Any) -> None:
    """Inject a custom side-query callable.

    The callable must accept a single ``dict`` argument with keys:
    ``model``, ``max_tokens``, ``maxRetries``, ``querySource``,
    ``messages``.
    """
    global _side_query_fn
    _side_query_fn = fn


async def _side_query(
    payload: dict[str, Any],
    *,
    timeout_s: float = _VALIDATION_TIMEOUT_S,
) -> Any:
    """Perform a lightweight API call to probe whether *model* is reachable.

    If ``_side_query_fn`` has been injected via :func:`set_side_query`, that
    implementation is used.  Otherwise, the call will raise
    ``NotImplementedError`` so the caller can handle it gracefully.
    """
    fn = _side_query_fn
    if fn is None:
        raise NotImplementedError(
            "sideQuery is not configured.  Call set_side_query() or set "
            "ANTHROPIC_CUSTOM_MODEL_OPTION to bypass the API probe."
        )

    try:
        if asyncio.iscoroutinefunction(fn):
            result = await asyncio.wait_for(
                fn(payload),
                timeout=timeout_s,
            )
        else:
            result = await asyncio.wait_for(
                asyncio.to_thread(fn, payload),
                timeout=timeout_s,
            )
        return result
    except asyncio.TimeoutError:
        raise ModelConnectionError(
            f"Timed out after {timeout_s:.0f}s while validating the model. "
            "Please check your network connection."
        ) from None


# ---------------------------------------------------------------------------
# Core validation
# ---------------------------------------------------------------------------


async def validate_model(
    model: str,
    *,
    probe_api: bool = True,
    check_deprecation: bool = True,
) -> ValidationResult:
    """Validate *model* and optionally probe it against the live API.

    Checks (in order):
    1. Empty string
    2. Allowlist
    3. Alias / custom model env-var (pre-validated)
    4. Deprecation warning (if *check_deprecation*)
    5. Cached result
    6. Live API probe (if *probe_api*)

    Parameters:
        model: The model string to validate.
        probe_api: If ``True`` (default), perform a live API call to confirm
            the model is reachable.  Set to ``False`` for offline checks.
        check_deprecation: If ``True`` (default), check the deprecation list.

    Returns:
        A :class:`ValidationResult` with the outcome.
    """
    # ------------------------------------------------------------------
    # 1. Empty string guard
    # ------------------------------------------------------------------
    normalized = model.strip()
    if not normalized:
        return ValidationResult(
            valid=False,
            error="Model name cannot be empty",
        )

    # ------------------------------------------------------------------
    # 2. Allowlist
    # ------------------------------------------------------------------
    if not is_model_allowed(normalized):
        return ValidationResult(
            valid=False,
            error=(
                f"Model {normalized!r} is not in the list of available models"
            ),
        )

    # ------------------------------------------------------------------
    # 3. Alias / custom env-var (no API call needed)
    # ------------------------------------------------------------------
    if _is_alias_or_custom(normalized):
        result = ValidationResult(valid=True)
        if check_deprecation and is_model_deprecated(normalized):
            result.deprecated = True
            result.deprecation_message = get_deprecation_message(normalized)
        return result

    # ------------------------------------------------------------------
    # 4. Deprecation check
    # ------------------------------------------------------------------
    deprecated = False
    deprecation_msg = ""
    if check_deprecation and is_model_deprecated(normalized):
        deprecated = True
        deprecation_msg = get_deprecation_message(normalized)

    # ------------------------------------------------------------------
    # 5. Cache lookup
    # ------------------------------------------------------------------
    cached = _cache_get(normalized)
    if cached is True:
        return ValidationResult(
            valid=True,
            deprecated=deprecated,
            deprecation_message=deprecation_msg,
        )
    if cached is False:
        return ValidationResult(
            valid=False,
            error=f"Model {normalized!r} was previously invalid",
            deprecated=deprecated,
            deprecation_message=deprecation_msg,
        )

    # ------------------------------------------------------------------
    # 6. Live API probe
    # ------------------------------------------------------------------
    if not probe_api:
        # When the caller opts out of the probe, treat as provisionally valid.
        _cache_set(normalized, True, ttl_s=300)  # short TTL — unverified
        return ValidationResult(
            valid=True,
            deprecated=deprecated,
            deprecation_message=deprecation_msg,
        )

    api_model = _normalize_model_for_api(normalized)

    try:
        await _side_query(
            {
                "model": api_model,
                "max_tokens": 1,
                "maxRetries": 0,
                "querySource": "model_validation",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "Hi",
                                "cache_control": {"type": "ephemeral"},
                            }
                        ],
                    }
                ],
            }
        )
    except NotImplementedError:
        # sideQuery is not wired up — treat as valid (the allowlist / alias
        # checks above already passed).
        logging.debug(
            "validate_model: sideQuery not configured; "
            "skipping API probe for %r",
            normalized,
        )
        _cache_set(normalized, True, ttl_s=300)
        return ValidationResult(
            valid=True,
            deprecated=deprecated,
            deprecation_message=deprecation_msg,
        )
    except Exception as exc:
        # Extract response body if available
        response_body = getattr(exc, "response_body", None)
        if response_body is None:
            response_body = getattr(exc, "body", None)

        classified = _classify_api_error(exc, response_body)

        if isinstance(classified, ModelNotFoundError):
            fallback = _get_3p_fallback_suggestion(api_model)
            suggestion = f". Try {fallback!r} instead" if fallback else ""
            _cache_set(normalized, False, ttl_s=60)  # short negative cache
            return ValidationResult(
                valid=False,
                error=f"Model {normalized!r} not found{suggestion}",
                deprecated=deprecated,
                deprecation_message=deprecation_msg,
                fallback=fallback,
            )

        if isinstance(classified, ModelAuthenticationError):
            # NEVER cache auth failures — the user might fix their key
            return ValidationResult(
                valid=False,
                error=(
                    "Authentication failed. "
                    "Please check your API credentials."
                ),
                deprecated=deprecated,
                deprecation_message=deprecation_msg,
            )

        if isinstance(classified, ModelConnectionError):
            return ValidationResult(
                valid=False,
                error=str(classified),
                deprecated=deprecated,
                deprecation_message=deprecation_msg,
            )

        if isinstance(classified, ModelRateLimitError):
            retry_msg = ""
            if classified.retry_after is not None:
                retry_msg = f" Retry after {classified.retry_after:.0f}s."
            return ValidationResult(
                valid=False,
                error=f"Rate limited.{retry_msg}",
                deprecated=deprecated,
                deprecation_message=deprecation_msg,
            )

        if isinstance(classified, ModelAPIError):
            return ValidationResult(
                valid=False,
                error=f"API error: {classified}",
                deprecated=deprecated,
                deprecation_message=deprecation_msg,
            )

        # Truly unknown — safe rejection
        err_msg = str(exc) or type(exc).__name__
        return ValidationResult(
            valid=False,
            error=f"Unable to validate model: {err_msg}",
            deprecated=deprecated,
            deprecation_message=deprecation_msg,
        )

    # --- API call succeeded ---
    _cache_set(normalized, True)
    return ValidationResult(
        valid=True,
        deprecated=deprecated,
        deprecation_message=deprecation_msg,
    )


# ---------------------------------------------------------------------------
# Legacy return-type compat (dict form)
# ---------------------------------------------------------------------------


async def validate_model_dict(model: str, **kwargs: Any) -> dict[str, bool | str]:
    """Backward-compatible wrapper that returns ``{valid, error}`` dict.

    This is kept for callers that expect the original dict-shaped return.
    New code should use :func:`validate_model` which returns a
    :class:`ValidationResult`.
    """
    result = await validate_model(model, **kwargs)
    d: dict[str, bool | str] = {"valid": result.valid}
    if result.error:
        d["error"] = result.error
    return d


# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------


async def is_valid_model(
    model: str,
    *,
    probe_api: bool = True,
) -> bool:
    """Return ``True`` if *model* passes validation.

    This is a convenience wrapper around :func:`validate_model` that only
    returns the boolean outcome.
    """
    result = await validate_model(model, probe_api=probe_api)
    return result.valid


async def validate_models(
    models: list[str],
    *,
    probe_api: bool = True,
    check_deprecation: bool = True,
    fail_fast: bool = False,
    concurrency: int = 5,
) -> dict[str, ValidationResult]:
    """Validate a batch of *models* concurrently.

    Parameters:
        models: Model strings to validate.
        probe_api: Forwarded to :func:`validate_model`.
        check_deprecation: Forwarded to :func:`validate_model`.
        fail_fast: If ``True``, abort remaining validations on the first failure.
        concurrency: Maximum number of concurrent API probes.

    Returns:
        A ``dict`` mapping each model string → its :class:`ValidationResult`.
    """
    results: dict[str, ValidationResult] = {}
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def _validate_one(m: str) -> None:
        async with semaphore:
            r = await validate_model(
                m,
                probe_api=probe_api,
                check_deprecation=check_deprecation,
            )
            results[m] = r

    if fail_fast:
        # Validate models one at a time so we can stop on the first failure
        # without wasting API calls.
        for m in models:
            await _validate_one(m)
            if m in results and not results[m].valid:
                # Mark the remaining models as skipped
                idx = models.index(m) + 1
                for remaining in models[idx:]:
                    if remaining not in results:
                        results[remaining] = ValidationResult(
                            valid=False,
                            error="Skipped due to earlier failure",
                        )
                break
    else:
        tasks = [asyncio.create_task(_validate_one(m)) for m in models]
        await asyncio.gather(*tasks, return_exceptions=True)

    # Ensure every requested model has a result (handles cancelled / crashed tasks)
    for m in models:
        if m not in results:
            results[m] = ValidationResult(
                valid=False,
                error="Validation task raised an unexpected exception",
            )

    return results


def get_cached_models() -> list[str]:
    """Return a list of model strings currently in the validation cache."""
    now = time.monotonic()
    # Prune expired entries while building the list
    expired = [k for k, (exp, _) in _valid_model_cache.items() if now > exp]
    for k in expired:
        del _valid_model_cache[k]
    return sorted(_valid_model_cache.keys())


def invalidate_model(model: str) -> None:
    """Remove a single model from the validation cache so it must be re-validated."""
    _cache_remove(model)


# ---------------------------------------------------------------------------
# Deprecated-model convenience
# ---------------------------------------------------------------------------


async def check_model_deprecation(model: str) -> tuple[bool, str]:
    """Check whether *model* is deprecated without a full validation round-trip.

    Returns:
        ``(is_deprecated, message)`` tuple.  ``message`` is empty when the
        model is not deprecated.
    """
    normalized = model.strip()
    if not normalized:
        return False, ""
    if is_model_deprecated(normalized):
        return True, get_deprecation_message(normalized)
    return False, ""


# ---------------------------------------------------------------------------
# Diagnostic helpers
# ---------------------------------------------------------------------------


def diagnose_validation_error(result: ValidationResult) -> dict[str, Any]:
    """Return a structured diagnostic dict from a :class:`ValidationResult`.

    Useful for logging, telemetry, and user-facing error messages.
    """
    diagnosis: dict[str, Any] = {
        "valid": result.valid,
        "error": result.error or None,
        "deprecated": result.deprecated,
        "deprecation_message": result.deprecation_message or None,
        "fallback": result.fallback,
        "fixes": [],
    }

    if not result.valid:
        err_lower = result.error.lower()

        if "empty" in err_lower:
            diagnosis["fixes"].append("Provide a non-empty model name")
        elif "not in the list" in err_lower:
            diagnosis["fixes"].append(
                "Choose a model from the available list or remove the allowlist restriction"
            )
        elif "not found" in err_lower:
            diagnosis["fixes"].append("Check the model name for typos")
            if result.fallback:
                diagnosis["fixes"].append(f"Use the fallback model: {result.fallback}")
        elif "authentication" in err_lower:
            diagnosis["fixes"].append("Verify your ANTHROPIC_API_KEY is set and valid")
            diagnosis["fixes"].append("Check that your API key has not expired or been revoked")
        elif "network" in err_lower or "connection" in err_lower or "timed out" in err_lower:
            diagnosis["fixes"].append("Check your internet connection")
            diagnosis["fixes"].append("Verify your proxy settings (HTTP_PROXY / HTTPS_PROXY)")
            diagnosis["fixes"].append("Try increasing the validation timeout")
        elif "rate limit" in err_lower:
            diagnosis["fixes"].append("Wait before retrying")
            diagnosis["fixes"].append(
                "Consider upgrading your API tier for higher rate limits"
            )

    if result.deprecated:
        diagnosis["fixes"].append(
            f"Upgrade to a newer model. {result.deprecation_message}"
        )

    return diagnosis
