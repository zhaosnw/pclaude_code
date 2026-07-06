"""Model context window and max-output helpers (`context.ts`).

Provides functions for determining context window sizes, max output tokens,
thinking budget, and automatic-compaction thresholds across all supported
models.  Handles environment-driven overrides, the ``[1m]`` context suffix,
ant-internal model tables, and capability-cache lookups.

.. note::
   *Context window* refers to the maximum input tokens the model accepts.
   *Max output tokens* refers to the hard server-side limit on completion
   length.  The two are independent – a 1M-context model may still have a
   32k output cap.
"""

from __future__ import annotations

import os
import re
from typing import Any

from hare.utils.config import get_global_config
from hare.utils.env_utils import is_env_truthy, is_env_defined_falsy
from hare.utils.model import get_canonical_name

# ---------------------------------------------------------------------------
# Constants (mirror TypeScript src/utils/context.ts)
# ---------------------------------------------------------------------------

MODEL_CONTEXT_WINDOW_DEFAULT = 200_000
"""Default context-window size for models whose true limit is unknown."""

COMPACT_MAX_OUTPUT_TOKENS = 20_000
"""Output-token limit used during /compact (summarisation) requests."""

MAX_OUTPUT_TOKENS_DEFAULT = 32_000
MAX_OUTPUT_TOKENS_UPPER_LIMIT = 64_000

CAPPED_DEFAULT_MAX_TOKENS = 8_000
"""Capped default for slot-reservation optimisation."""
ESCALATED_MAX_TOKENS = 64_000
"""Retry limit when the capped default is exhausted."""

CONTEXT_1M_BETA_HEADER = "context-1m-2025-08-07"
"""Beta-header string that enables the 1M-context preview on supported models."""

# Minimum reasonable context window (protects against bad config)
_MIN_SAFE_CONTEXT_WINDOW = 4_000
# Maximum we will ever report (beyond 1M is unrealistic for current models)
_MAX_SAFE_CONTEXT_WINDOW = 2_000_000


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _is_ant() -> bool:
    """Check whether the current environment is an ant (internal) deployment."""
    return os.environ.get("USER_TYPE") == "ant"


def _parse_positive_int(raw: str | None) -> int | None:
    """Parse *raw* as a positive integer; return *None* on failure."""
    if raw is None:
        return None
    try:
        val = int(raw, 10)
        return val if val > 0 else None
    except (ValueError, TypeError):
        return None


def _validate_model_arg(model: str) -> str:
    """Return *model* after basic sanitisation; raises on obviously bad input."""
    if not isinstance(model, str) or not model.strip():
        raise ValueError("model must be a non-empty string")
    return model.strip()


def _get_model_capability(model: str) -> dict[str, Any] | None:
    """Retrieve the capability record for *model* and return it as a dict.

    Attempts the following in order:

    1. The real ``get_capabilities_for_model`` function (returns a
       ``ModelCapabilities`` dataclass with ``max_input_tokens`` /
       ``max_tokens`` when available).
    2. Falls back to a stub that returns *None* when the capability
       infrastructure is not yet fully wired.

    Callers should always guard with ``if cap and cap.get(...)`` before
    indexing.
    """
    if not model or not model.strip():
        return None

    # --- Real lookup (model_capabilities.py) -------------------------------
    try:
        from hare.utils.model.model_capabilities import get_capabilities_for_model

        caps = get_capabilities_for_model(model)
        if caps is None:
            return None
        # Build a dict with the fields callers expect.
        result: dict[str, Any] = {}
        for attr in ("max_input_tokens", "max_tokens", "max_output_tokens",
                      "vision", "tools", "computer_use"):
            val = getattr(caps, attr, None)
            if val is not None:
                result[attr] = val
        return result if result else None
    except Exception:
        pass

    # --- Fallback: try the scalar accessor --------------------------------
    try:
        from hare.utils.model.model_capabilities import get_model_capability

        max_input = get_model_capability(model, "max_input_tokens")
        max_out = get_model_capability(model, "max_tokens")
        if max_input is None and max_out is None:
            return None
        result = {}
        if isinstance(max_input, (int, float)):
            result["max_input_tokens"] = int(max_input)
        if isinstance(max_out, (int, float)):
            result["max_tokens"] = int(max_out)
        return result if result else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Ant-internal model table
# ---------------------------------------------------------------------------

# Type alias kept light-weight; ant models are represented as plain dicts.
_ANT_MODEL_TABLE: list[dict[str, Any]] | None = None


def _build_ant_model_table() -> list[dict[str, Any]]:
    """Construct (or retrieve cached) ant-internal model definitions.

    Mirrors the Granite-Belt / GrowthBook ``tengu_ant_model_override``
    feature in TypeScript.  The model definitions are static for the
    open-source build and may be overridden by a growth-book feature flag
    in internal deployments.
    """
    global _ANT_MODEL_TABLE
    if _ANT_MODEL_TABLE is not None:
        return _ANT_MODEL_TABLE

    if not _is_ant():
        _ANT_MODEL_TABLE = []
        return _ANT_MODEL_TABLE

    # Try to load from GrowthBook override first (ant-only path).
    try:
        from hare.utils.model.ant_models import is_ant_user
        if is_ant_user():
            # Attempt dynamic override (mirrors getAntModelOverrideConfig in TS).
            try:
                # noinspection PyPackageRequirements
                from hare.services.analytics.growthbook import (  # type: ignore[import-untyped]
                    get_feature_value,
                )

                override = get_feature_value("tengu_ant_model_override", None)
                if isinstance(override, dict) and isinstance(override.get("antModels"), list):
                    _ANT_MODEL_TABLE = override["antModels"]
                    return _ANT_MODEL_TABLE
            except Exception:
                pass
    except Exception:
        pass

    # Static fallback table (mirrors the defaults supplied by antModels.ts).
    _ANT_MODEL_TABLE = [
        {
            "alias": "opus46",
            "model": "claude-opus-4-6-20260301",
            "label": "Opus 4.6",
            "contextWindow": 200_000,
            "defaultMaxTokens": 64_000,
            "upperMaxTokensLimit": 128_000,
            "alwaysOnThinking": True,
        },
        {
            "alias": "sonnet46",
            "model": "claude-sonnet-4-6-20260301",
            "label": "Sonnet 4.6",
            "contextWindow": 200_000,
            "defaultMaxTokens": 32_000,
            "upperMaxTokensLimit": 128_000,
        },
        {
            "alias": "opus45",
            "model": "claude-opus-4-5-20250514",
            "label": "Opus 4.5",
            "contextWindow": 200_000,
            "defaultMaxTokens": 32_000,
            "upperMaxTokensLimit": 64_000,
        },
        {
            "alias": "sonnet45",
            "model": "claude-sonnet-4-5-20241022",
            "label": "Sonnet 4.5",
            "contextWindow": 200_000,
            "defaultMaxTokens": 32_000,
            "upperMaxTokensLimit": 64_000,
        },
        {
            "alias": "haiku45",
            "model": "claude-haiku-4-5-20250514",
            "label": "Haiku 4.5",
            "contextWindow": 200_000,
            "defaultMaxTokens": 8_192,
            "upperMaxTokensLimit": 8_192,
        },
        {
            "alias": "sonnet37",
            "model": "hare-3-7-sonnet-20250219",
            "label": "Sonnet 3.7",
            "contextWindow": 200_000,
            "defaultMaxTokens": 8_192,
            "upperMaxTokensLimit": 8_192,
        },
        {
            "alias": "sonnet35",
            "model": "hare-3-5-sonnet-20241022",
            "label": "Sonnet 3.5",
            "contextWindow": 200_000,
            "defaultMaxTokens": 8_192,
            "upperMaxTokensLimit": 8_192,
        },
        {
            "alias": "haiku35",
            "model": "hare-3-5-haiku-20241022",
            "label": "Haiku 3.5",
            "contextWindow": 200_000,
            "defaultMaxTokens": 4_096,
            "upperMaxTokensLimit": 4_096,
        },
    ]
    return _ANT_MODEL_TABLE


def _resolve_ant_model(model: str) -> dict[str, Any] | None:
    """Look up *model* in the ant-internal model table.

    Returns the matching ant-model dictionary or *None* when *model* is
    not recognised or the caller is not an ant user.
    """
    if not _is_ant():
        return None
    if not model or not model.strip():
        return None

    lower = model.strip().lower()
    for entry in _build_ant_model_table():
        alias = (entry.get("alias") or "").lower()
        m = (entry.get("model") or "").lower()
        if lower == alias or lower in m or m in lower:
            return entry
    return None


def get_ant_models() -> list[dict[str, Any]]:
    """Return the full list of ant-internal model definitions."""
    return list(_build_ant_model_table())


# ---------------------------------------------------------------------------
# 1M-context detection
# ---------------------------------------------------------------------------

def is_1m_context_disabled() -> bool:
    """Return *True* when the ``CLAUDE_CODE_DISABLE_1M_CONTEXT`` env var is set.

    Used by C4E admins to disable 1M context for HIPAA compliance.
    """
    return is_env_truthy(os.environ.get("CLAUDE_CODE_DISABLE_1M_CONTEXT"))


def has_1m_context(model: str) -> bool:
    """Return *True* when *model* carries an explicit ``[1m]`` suffix."""
    if is_1m_context_disabled():
        return False
    return "[1m]" in model.lower()


def model_supports_1m(model: str) -> bool:
    """Return *True* when *model* is known to support the 1M-context beta.

    Currently covers Opus 4.6 and Sonnet 4-series models.
    """
    if is_1m_context_disabled():
        return False
    c = get_canonical_name(model)
    return "claude-sonnet-4" in c or "opus-4-6" in c


# ---------------------------------------------------------------------------
# Sonnet 1M experiment treatment
# ---------------------------------------------------------------------------

def get_sonnet_1m_exp_treatment_enabled(model: str) -> bool:
    """Check whether the coral-reef Sonnet 1M experiment treatment is active.

    The treatment enables 1M context for Sonnet 4.6 users who have not
    explicitly opted in via ``[1m]``.
    """
    if is_1m_context_disabled() or has_1m_context(model):
        return False
    if "sonnet-4-6" not in get_canonical_name(model):
        return False
    gc = get_global_config()
    cache = getattr(gc, "client_data_cache", None)
    if not isinstance(cache, dict):
        cache = {}
    return cache.get("coral_reef_sonnet") == "true"


# ---------------------------------------------------------------------------
# Context-window resolution
# ---------------------------------------------------------------------------

def get_context_window_for_model(model: str, betas: list[str] | None = None) -> int:
    """Return the effective context-window size for *model*.

    Resolution order (first match wins):

    1. ``CLAUDE_CODE_MAX_CONTEXT_TOKENS`` env override (ant-only).
    2. Explicit ``[1m]`` suffix on the model name.
    3. Model-capability cache (``max_input_tokens >= 100_000``).
    4. ``context-1m-2025-08-07`` beta header + 1M-capable model.
    5. Sonnet 1M experiment treatment.
    6. Ant-internal model table.
    7. ``MODEL_CONTEXT_WINDOW_DEFAULT`` (200k).

    Returns a value clamped to ``[_MIN_SAFE_CONTEXT_WINDOW,
    _MAX_SAFE_CONTEXT_WINDOW]``.
    """
    _validate_model_arg(model)

    # 1. Environment override (ant-only) — takes absolute precedence.
    if _is_ant():
        override = _parse_positive_int(os.environ.get("CLAUDE_CODE_MAX_CONTEXT_TOKENS"))
        if override is not None:
            return _clamp_window(override)

    # 2. Explicit [1m] suffix.
    if has_1m_context(model):
        return _clamp_window(1_000_000)

    # 3. Model-capability cache.
    cap = _get_model_capability(model)
    if cap:
        mit_raw = cap.get("max_input_tokens")
        if mit_raw is not None:
            try:
                mit = int(mit_raw)
            except (TypeError, ValueError):
                mit = 0
            if mit >= 100_000:
                if mit > MODEL_CONTEXT_WINDOW_DEFAULT and is_1m_context_disabled():
                    return _clamp_window(MODEL_CONTEXT_WINDOW_DEFAULT)
                return _clamp_window(mit)

    # 4. Beta-header hint.
    betas = betas or []
    if CONTEXT_1M_BETA_HEADER in betas and model_supports_1m(model):
        return _clamp_window(1_000_000)

    # 5. Sonnet 1M experiment.
    if get_sonnet_1m_exp_treatment_enabled(model):
        return _clamp_window(1_000_000)

    # 6. Ant-internal model table.
    if _is_ant():
        am = _resolve_ant_model(model)
        if am and am.get("contextWindow"):
            try:
                return _clamp_window(int(am["contextWindow"]))
            except (TypeError, ValueError):
                pass

    # 7. Default.
    return _clamp_window(MODEL_CONTEXT_WINDOW_DEFAULT)


def _clamp_window(window: int) -> int:
    """Clamp *window* to the safe range."""
    return max(_MIN_SAFE_CONTEXT_WINDOW, min(window, _MAX_SAFE_CONTEXT_WINDOW))


# ---------------------------------------------------------------------------
# Context-window percentages
# ---------------------------------------------------------------------------

def calculate_context_percentages(
    current_usage: dict[str, int] | None,
    context_window_size: int,
) -> dict[str, int | None]:
    """Compute context-window usage and remaining percentages.

    Parameters
    ----------
    current_usage:
        Dict with keys ``input_tokens``, ``cache_creation_input_tokens``,
        ``cache_read_input_tokens`` (all optional; missing keys treated as
        zero).
    context_window_size:
        The total context window in tokens.  Must be > 0.

    Returns
    -------
    dict
        ``{"used": int|None, "remaining": int|None}``.  Percentages are
        clamped to ``[0, 100]``.  Returns *None* values when no usage data
        is provided.
    """
    if not current_usage or context_window_size <= 0:
        return {"used": None, "remaining": None}

    try:
        total = (
            current_usage.get("input_tokens", 0)
            + current_usage.get("cache_creation_input_tokens", 0)
            + current_usage.get("cache_read_input_tokens", 0)
        )
    except (TypeError, AttributeError):
        return {"used": None, "remaining": None}

    used_pct = round((total / context_window_size) * 100)
    clamped = min(100, max(0, used_pct))
    return {"used": clamped, "remaining": 100 - clamped}


# ---------------------------------------------------------------------------
# Max output tokens per model
# ---------------------------------------------------------------------------

def get_model_max_output_tokens(model: str) -> dict[str, int]:
    """Return ``{default, upperLimit}`` max output tokens for *model*.

    Resolution order:

    1. Ant-internal model table (``USER_TYPE == "ant"``).
    2. Model-family heuristics (canonical-name substring checks).
    3. Model-capability cache override (``max_tokens >= 4096``).
    4. Constants ``MAX_OUTPUT_TOKENS_DEFAULT`` / ``MAX_OUTPUT_TOKENS_UPPER_LIMIT``.
    """
    _validate_model_arg(model)

    # 1. Ant-internal model table.
    if _is_ant():
        am = _resolve_ant_model(model.lower())
        if am:
            default_tokens = int(
                am.get("defaultMaxTokens") or MAX_OUTPUT_TOKENS_DEFAULT
            )
            upper_limit = int(
                am.get("upperMaxTokensLimit") or MAX_OUTPUT_TOKENS_UPPER_LIMIT
            )
            return _validate_output_pair(default_tokens, upper_limit)

    # 2. Canonical-name heuristics.
    m = get_canonical_name(model)
    default_tokens, upper_limit = _resolve_output_by_family(m)

    # 3. Capability-cache override.
    cap = _get_model_capability(model)
    if cap:
        mt_raw = cap.get("max_tokens")
        if mt_raw is not None:
            try:
                mt = int(mt_raw)
            except (TypeError, ValueError):
                mt = 0
            if mt >= 4_096:
                upper_limit = mt
                default_tokens = min(default_tokens, upper_limit)

    return _validate_output_pair(default_tokens, upper_limit)


def _resolve_output_by_family(canonical: str) -> tuple[int, int]:
    """Determine (default, upperLimit) from the canonical short name.

    Mapping mirrors the TypeScript ``getModelMaxOutputTokens`` switch.
    """
    # Ordered most-specific first to avoid false substring matches.
    checks: list[tuple[str, int, int]] = [
        ("opus-4-6", 64_000, 128_000),
        ("sonnet-4-6", 32_000, 128_000),
        ("opus-4-5", 32_000, 64_000),
        ("sonnet-4", 32_000, 64_000),   # matches sonnet-4-5, sonnet-4, sonnet-4-6 eaten above
        ("haiku-4", 32_000, 64_000),
        ("opus-4-1", 32_000, 32_000),
        ("opus-4", 32_000, 32_000),     # catch-all opus-4 (after 4-6, 4-5, 4-1)
        ("hare-3-opus", 4_096, 4_096),
        ("hare-3-sonnet", 8_192, 8_192),
        ("hare-3-haiku", 4_096, 4_096),
        ("3-5-sonnet", 8_192, 8_192),
        ("3-5-haiku", 8_192, 8_192),
        ("3-7-sonnet", 32_000, 64_000),
    ]
    for fragment, dt, ul in checks:
        if fragment in canonical:
            return dt, ul
    return MAX_OUTPUT_TOKENS_DEFAULT, MAX_OUTPUT_TOKENS_UPPER_LIMIT


def _validate_output_pair(default_tokens: int, upper_limit: int) -> dict[str, int]:
    """Sanity-check and return a valid ``{default, upperLimit}`` pair."""
    default_tokens = max(1, default_tokens)
    upper_limit = max(default_tokens, upper_limit)
    return {"default": default_tokens, "upperLimit": upper_limit}


# ---------------------------------------------------------------------------
# Max thinking tokens
# ---------------------------------------------------------------------------

def get_max_thinking_tokens_for_model(model: str) -> int:
    """Return the maximum thinking budget (``upperLimit - 1``) for *model*.

    The budget must be strictly less than the max output tokens.
    """
    o = get_model_max_output_tokens(model)
    return max(0, o["upperLimit"] - 1)


# ---------------------------------------------------------------------------
# Expanded utilities — auto-compact, token estimation, clamping
# ---------------------------------------------------------------------------

def get_auto_compact_threshold(
    model: str,
    betas: list[str] | None = None,
    *,
    fraction: float = 0.85,
) -> int:
    """Return the context-usage threshold (in tokens) that triggers auto-compact.

    By default the threshold is 85 % of the effective context window.
    The result is guaranteed to be at least ``COMPACT_MAX_OUTPUT_TOKENS``
    so there is always room for the compact response.

    Parameters
    ----------
    model:
        Model name (may include ``[1m]`` suffix).
    betas:
        Beta headers present on the request.
    fraction:
        Fraction of the context window to use as threshold (default 0.85).
    """
    window = get_context_window_for_model(model, betas)
    threshold = int(window * fraction)
    return max(threshold, COMPACT_MAX_OUTPUT_TOKENS)


def get_effective_max_output_tokens(
    model: str,
    *,
    capped: bool = True,
) -> dict[str, int]:
    """Return the effective max output tokens for *model*.

    When *capped* is *True* the default is clamped to
    ``CAPPED_DEFAULT_MAX_TOKENS`` for slot-reservation efficiency,
    mirroring the TypeScript ``getMaxOutputTokensForModel`` behaviour in
    ``claude.ts``.
    """
    info = get_model_max_output_tokens(model)
    default_tokens = info["default"]
    upper_limit = info["upperLimit"]

    if capped and default_tokens > CAPPED_DEFAULT_MAX_TOKENS:
        default_tokens = CAPPED_DEFAULT_MAX_TOKENS

    return {"default": default_tokens, "upperLimit": upper_limit}


def clamp_output_tokens(requested: int, model: str) -> int:
    """Clamp *requested* output tokens to the valid range for *model*.

    - Minimum: 1.
    - Maximum: the model's ``upperLimit``.
    - Default (when *requested* is <= 0): the model's effective default.
    """
    info = get_model_max_output_tokens(model)
    upper = info["upperLimit"]
    default = info["default"]
    if requested <= 0:
        return default
    return max(1, min(requested, upper))


def estimate_token_count(text: str) -> int:
    """Return a rough token-count estimate for *text*.

    Uses a simple heuristic: ~4 characters per token for English text.
    For precise token counts use a dedicated tokenizer (e.g. tiktoken).

    This mirrors the convention used in several TypeScript utility
    functions for quick budget checks before invoking the tokenizer.
    """
    if not text:
        return 0
    # Heuristic: ~4 characters per token for English text.
    # Non-ASCII / CJK characters count as ~1.5 tokens each.
    ascii_count = sum(1 for c in text if ord(c) < 128)
    non_ascii_count = len(text) - ascii_count
    return max(1, int(ascii_count / 4 + non_ascii_count / 1.5))


def estimate_message_tokens(message: dict[str, Any]) -> int:
    """Estimate the token count of a single message dict (rough heuristic).

    Adds a small overhead per message role and per tool-call / tool-result
    block to mirror the padding applied by real tokenizers.
    """
    tokens = 4  # per-message overhead (role + framing)
    content = message.get("content", "")

    if isinstance(content, str):
        tokens += estimate_token_count(content)
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                block_text = block.get("text") or ""
                if block.get("type") == "tool_use":
                    block_text += " " + (block.get("name") or "")
                    block_text += " " + (block.get("input") or "")
                tokens += estimate_token_count(str(block_text))
            elif isinstance(block, str):
                tokens += estimate_token_count(block)

    # Tool-call and tool-result blocks carry extra framing overhead.
    tool_calls = message.get("tool_calls") or []
    for _ in tool_calls:
        tokens += 20  # approximate overhead per tool call block
    return max(1, tokens)


def estimate_tokens_from_messages(messages: list[dict[str, Any]]) -> int:
    """Estimate total input tokens for a list of messages.

    Uses ``estimate_message_tokens`` on each message plus a fixed system
    overhead.  For a precise count use a real tokenizer.
    """
    if not messages:
        return 0
    total = 8  # system / framing overhead
    for msg in messages:
        total += estimate_message_tokens(msg)
    return max(1, total)


# ---------------------------------------------------------------------------
# Context-window info bundle
# ---------------------------------------------------------------------------

def get_context_window_info(
    model: str,
    betas: list[str] | None = None,
) -> dict[str, Any]:
    """Return a comprehensive context-window summary for *model*.

    Includes effective window, output limits, compact threshold, and
    diagnostic flags.
    """
    window = get_context_window_for_model(model, betas)
    output = get_model_max_output_tokens(model)
    thinking = get_max_thinking_tokens_for_model(model)
    compact_threshold = get_auto_compact_threshold(model, betas)
    eff_output = get_effective_max_output_tokens(model, capped=True)

    return {
        "contextWindow": window,
        "isDefault": window == MODEL_CONTEXT_WINDOW_DEFAULT,
        "is1M": window >= 1_000_000,
        "maxOutputTokens": output,
        "effectiveOutputTokens": eff_output,
        "maxThinkingTokens": thinking,
        "autoCompactThreshold": compact_threshold,
        "modelSupports1M": model_supports_1m(model),
        "hasExplicit1M": has_1m_context(model),
    }


# ---------------------------------------------------------------------------
# Validation / diagnostic helpers
# ---------------------------------------------------------------------------

def is_context_window_customized(model: str, betas: list[str] | None = None) -> bool:
    """Return *True* when the context window for *model* differs from the default."""
    return get_context_window_for_model(model, betas) != MODEL_CONTEXT_WINDOW_DEFAULT


def is_model_eligible_for_1m(model: str, betas: list[str] | None = None) -> bool:
    """Return *True* when *model* can receive a 1M context window.

    This is the union of all 1M-eligibility checks: explicit ``[1m]``
    suffix, beta header, known 1M-capable model, and experiment treatment.
    """
    return get_context_window_for_model(model, betas) >= 1_000_000


def get_context_usage_banner_text(
    used_pct: int | None,
    *,
    warning_threshold: int = 85,
    danger_threshold: int = 95,
) -> str | None:
    """Return a human-readable context-usage banner string, or *None*.

    Parameters
    ----------
    used_pct:
        Context-usage percentage (0–100), or *None* if unknown.
    warning_threshold:
        Percent at which to show a warning.
    danger_threshold:
        Percent at which to show a danger-level notice.
    """
    if used_pct is None:
        return None
    if used_pct >= danger_threshold:
        return (
            f"Context window {used_pct}% full.  "
            "Consider running /compact or starting a new conversation."
        )
    if used_pct >= warning_threshold:
        return f"Context window at {used_pct}%.  Consider running /compact soon."
    return None


def get_remaining_context_tokens(
    current_usage: dict[str, int] | None,
    context_window_size: int,
) -> int | None:
    """Return the estimated number of remaining input tokens."""
    if not current_usage or context_window_size <= 0:
        return None
    try:
        total = (
            current_usage.get("input_tokens", 0)
            + current_usage.get("cache_creation_input_tokens", 0)
            + current_usage.get("cache_read_input_tokens", 0)
        )
    except (TypeError, AttributeError):
        return None
    return max(0, context_window_size - total)


def should_auto_compact(
    current_usage: dict[str, int] | None,
    model: str,
    betas: list[str] | None = None,
    *,
    threshold_fraction: float = 0.85,
) -> bool:
    """Return *True* when an auto-compact should be triggered.

    Evaluates whether the current context usage exceeds the threshold
    for the given model.
    """
    if not current_usage:
        return False
    percentages = calculate_context_percentages(
        current_usage,
        get_context_window_for_model(model, betas),
    )
    used = percentages.get("used")
    if used is None:
        return False
    return used >= int(threshold_fraction * 100)


def get_compact_max_tokens_for_model(model: str) -> int:
    """Return the output token limit to use when calling the model for compaction.

    This is always ``COMPACT_MAX_OUTPUT_TOKENS``, clamped by the model's
    actual upper limit.
    """
    upper = get_model_max_output_tokens(model)["upperLimit"]
    return min(COMPACT_MAX_OUTPUT_TOKENS, upper)


def get_escalated_output_tokens(model: str) -> int:
    """Return the escalated (retry) max output tokens for *model*.

    When the capped default is exhausted, requests are retried at this
    higher limit.
    """
    upper = get_model_max_output_tokens(model)["upperLimit"]
    return min(ESCALATED_MAX_TOKENS, upper)


def is_output_capped(model: str) -> bool:
    """Return *True* when the effective default output tokens for *model* are
    capped below the native default (slot-reservation optimisation)."""
    native = get_model_max_output_tokens(model)["default"]
    return native > CAPPED_DEFAULT_MAX_TOKENS
