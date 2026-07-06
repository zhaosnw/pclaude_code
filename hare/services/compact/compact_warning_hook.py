"""
Compaction warning hook with GrowthBook feature-flag gating.

Port of: src/services/compact/compactWarningHook.ts +
         src/services/compact/autoCompact.ts (calculateTokenWarningState)

Provides warning-level determination, suppression management, growthbook-gated
configuration, cooldown mechanics, blocking-threshold checks, and human-readable
warning messages for surfacing compaction urgency during a conversation session.

GrowthBook feature flags (namespace: compact_warning):
  - compact_warning_enabled           — master on/off (bool, default True)
  - compact_warning_token_threshold   — override token count at which
                                        warnings fire (int, 0 = use auto)
  - compact_warning_suppressible      — allow user suppression (bool, default True)
  - compact_warning_cooldown_seconds  — minimum seconds between repeated
                                        warnings (int, default 300)
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from hare.services.analytics.growthbook import (
    check_gate_cached_or_blocking,
    get_feature_value,
    is_feature_enabled,
    set_cached_feature_value,
)
from hare.services.compact.compact_warning_state import (
    clear_compact_warning_suppression,
    is_compact_warning_suppressed,
    suppress_compact_warning,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants (mirrors autoCompact.ts)
# ---------------------------------------------------------------------------

WARNING_THRESHOLD_BUFFER_TOKENS = 20_000
ERROR_THRESHOLD_BUFFER_TOKENS = 20_000
MANUAL_COMPACT_BUFFER_TOKENS = 3_000
AUTOCOMPACT_BUFFER_TOKENS = 13_000
MAX_OUTPUT_TOKENS_FOR_SUMMARY = 20_000
MODEL_CONTEXT_WINDOW_DEFAULT = 200_000


# ---------------------------------------------------------------------------
# Warning severity
# ---------------------------------------------------------------------------

class CompactWarningLevel(Enum):
    """Severity of a compaction warning."""

    NONE = "none"
    INFO = "info"       # Approaching threshold; gentle heads-up
    WARN = "warn"       # Close to threshold; action recommended
    ERROR = "error"     # At or over threshold; compaction required soon
    BLOCK = "block"     # Context is critically full; blocking further input


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Valid percentage range for threshold fractions
_MIN_PERCENT = 0.01
_MAX_PERCENT = 1.0


@dataclass
class CompactWarningConfig:
    """GrowthBook-driven configuration for compaction warnings.

    All fields can be overridden by GrowthBook feature flags.
    """

    enabled: bool = True
    suppressible: bool = True
    cooldown_seconds: int = 300  # 5 minutes between repeated warnings
    token_threshold: int = 0     # 0 = auto-calculate from context window
    warn_percent: float = 0.75   # fraction of threshold → WARN level
    error_percent: float = 0.90  # fraction of threshold → ERROR level
    info_percent: float = 0.60   # fraction of threshold → INFO level
    block_percent: float = 0.97  # fraction of threshold → BLOCK level (mandatory)

    def validate(self) -> list[str]:
        """Validate that percentage thresholds are in sensible ranges.

        Returns a list of validation error messages (empty = valid).
        """
        errors: list[str] = []
        for name in ("info_percent", "warn_percent", "error_percent", "block_percent"):
            val = getattr(self, name, 0.0)
            if not (_MIN_PERCENT <= val <= _MAX_PERCENT):
                errors.append(
                    f"{name}={val} is outside valid range "
                    f"[{_MIN_PERCENT}, {_MAX_PERCENT}]"
                )
        # Ordering constraint: info < warn < error < block
        if not (self.info_percent <= self.warn_percent <= self.error_percent <= self.block_percent):
            errors.append(
                f"Percentage ordering violated: info={self.info_percent} "
                f"warn={self.warn_percent} error={self.error_percent} "
                f"block={self.block_percent} (expect info <= warn <= error <= block)"
            )
        if self.cooldown_seconds < 0:
            errors.append(f"cooldown_seconds={self.cooldown_seconds} must be >= 0")
        if self.token_threshold < 0:
            errors.append(f"token_threshold={self.token_threshold} must be >= 0")
        return errors

    def is_valid(self) -> bool:
        """Return True when config passes all validation checks."""
        return len(self.validate()) == 0


_DEFAULT_CONFIG = CompactWarningConfig()

# Internal cooldown tracker (module-level — per-process, not per-session)
_last_warning_time: float = 0.0

# Per-session cooldown dict keyed by session_id (optional session-scoped mode)
_session_cooldowns: dict[str, float] = {}


# ---------------------------------------------------------------------------
# GrowthBook config reader
# ---------------------------------------------------------------------------

def _get_compact_warning_config() -> CompactWarningConfig:
    """Fetch the current warning config from GrowthBook feature flags.

    Reads the ``compact_warning`` feature flag object, merging its keys
    on top of defaults.  Falls back to defaults when the flag is absent
    or not a dict.  Silently clamps out-of-range values.

    Env-var overrides (for testing / local dev):
      CLAUDE_CODE_COMPACT_WARNING_ENABLED=0|1
      CLAUDE_CODE_COMPACT_WARNING_THRESHOLD=<int>
      CLAUDE_CODE_COMPACT_WARNING_COOLDOWN=<int>
    """
    try:
        raw = get_feature_value("compact_warning", None)
    except Exception:
        logger.debug("GrowthBook fetch for compact_warning failed; using defaults")
        raw = None

    if not isinstance(raw, dict):
        config = _DEFAULT_CONFIG
    else:
        config = CompactWarningConfig(
            enabled=bool(raw.get("enabled", _DEFAULT_CONFIG.enabled)),
            suppressible=bool(
                raw.get("suppressible", _DEFAULT_CONFIG.suppressible)
            ),
            cooldown_seconds=_clamp_int(
                int(raw.get("cooldownSeconds", _DEFAULT_CONFIG.cooldown_seconds)),
                min_val=0,
            ),
            token_threshold=_clamp_int(
                int(raw.get("tokenThreshold", _DEFAULT_CONFIG.token_threshold)),
                min_val=0,
            ),
            warn_percent=_clamp_percent(
                float(raw.get("warnPercent", _DEFAULT_CONFIG.warn_percent))
            ),
            error_percent=_clamp_percent(
                float(raw.get("errorPercent", _DEFAULT_CONFIG.error_percent))
            ),
            info_percent=_clamp_percent(
                float(raw.get("infoPercent", _DEFAULT_CONFIG.info_percent))
            ),
            block_percent=_clamp_percent(
                float(raw.get("blockPercent", _DEFAULT_CONFIG.block_percent))
            ),
        )

    # Apply env-var overrides
    config = _apply_env_overrides(config)

    # Auto-fix ordering if GrowthBook delivered bad values
    # (enforce info <= warn <= error <= block)
    if config.info_percent > config.warn_percent:
        config.info_percent = config.warn_percent
    if config.warn_percent > config.error_percent:
        config.warn_percent = config.error_percent
    if config.error_percent > config.block_percent:
        config.error_percent = config.block_percent

    return config


# ---------------------------------------------------------------------------
# Helpers: value clamping
# ---------------------------------------------------------------------------

def _clamp_percent(value: float) -> float:
    """Clamp a percentage value to [0, 1]."""
    if value != value:  # NaN check
        return 0.0
    return max(0.0, min(1.0, value))


def _clamp_int(value: int, min_val: int = 0, max_val: int = 10_000_000) -> int:
    """Clamp an integer to [min_val, max_val]."""
    return max(min_val, min(max_val, value))


def _apply_env_overrides(config: CompactWarningConfig) -> CompactWarningConfig:
    """Apply environment-variable overrides to the config (for testing)."""
    # Master enable/disable
    env_enabled = os.environ.get("CLAUDE_CODE_COMPACT_WARNING_ENABLED")
    if env_enabled is not None:
        config.enabled = env_enabled.lower() in ("1", "true", "yes", "on")

    # Token threshold override
    env_threshold = os.environ.get("CLAUDE_CODE_COMPACT_WARNING_THRESHOLD")
    if env_threshold is not None:
        try:
            config.token_threshold = _clamp_int(int(env_threshold), min_val=0)
        except ValueError:
            pass

    # Cooldown override
    env_cooldown = os.environ.get("CLAUDE_CODE_COMPACT_WARNING_COOLDOWN")
    if env_cooldown is not None:
        try:
            config.cooldown_seconds = _clamp_int(int(env_cooldown), min_val=0)
        except ValueError:
            pass

    # Warning percentage override
    env_pct = os.environ.get("CLAUDE_CODE_COMPACT_WARNING_PCT")
    if env_pct is not None:
        try:
            config.warn_percent = _clamp_percent(float(env_pct) / 100.0)
        except ValueError:
            pass

    return config


def _get_effective_threshold(
    config: CompactWarningConfig,
    token_threshold: int,
) -> int:
    """Resolve the effective token threshold.

    When ``config.token_threshold`` > 0 it takes precedence (GrowthBook override).
    Otherwise the caller-supplied ``token_threshold`` (e.g. context window * 0.80) wins.
    Returns 0 when no usable threshold can be determined (feature effectively disabled).
    """
    if config.token_threshold > 0:
        return config.token_threshold
    return max(0, token_threshold)


# ---------------------------------------------------------------------------
# Context window resolution
# ---------------------------------------------------------------------------

def get_effective_context_window_size(model: str = "") -> int:
    """Return the context window size minus reserved output tokens.

    Mirrors ``getEffectiveContextWindowSize`` from autoCompact.ts.
    When *model* is empty, falls back to ``MODEL_CONTEXT_WINDOW_DEFAULT``.
    """
    # Try to get the actual context window for the model
    try:
        from hare.utils.context import get_context_window_for_model

        context_window = get_context_window_for_model(model)
    except Exception:
        context_window = MODEL_CONTEXT_WINDOW_DEFAULT

    # Apply env override for testing
    auto_window = os.environ.get("CLAUDE_CODE_AUTO_COMPACT_WINDOW")
    if auto_window:
        try:
            parsed = int(auto_window, 10)
            if parsed > 0:
                context_window = min(context_window, parsed)
        except ValueError:
            pass

    # Reserve tokens for compaction summary output
    try:
        from hare.services.api.claude import get_max_output_tokens_for_model

        max_output = get_max_output_tokens_for_model(model)
    except Exception:
        max_output = MAX_OUTPUT_TOKENS_FOR_SUMMARY

    reserved = min(max_output, MAX_OUTPUT_TOKENS_FOR_SUMMARY)
    return max(0, context_window - reserved)


def get_auto_compact_threshold(model: str = "") -> int:
    """Effective window minus the auto-compact buffer."""
    effective = get_effective_context_window_size(model)
    return max(0, effective - AUTOCOMPACT_BUFFER_TOKENS)


def get_warning_threshold(model: str = "") -> int:
    """Effective window minus the warning buffer."""
    effective = get_effective_context_window_size(model)
    return max(0, effective - WARNING_THRESHOLD_BUFFER_TOKENS)


def get_error_threshold(model: str = "") -> int:
    """Effective window minus the error buffer."""
    effective = get_effective_context_window_size(model)
    return max(0, effective - ERROR_THRESHOLD_BUFFER_TOKENS)


def get_blocking_threshold(model: str = "") -> int:
    """Threshold at which compaction becomes mandatory (blocking).

    This is the actual context window minus a small manual-compact buffer,
    mirroring the ``isAtBlockingLimit`` logic in autoCompact.ts.
    """
    effective = get_effective_context_window_size(model)
    # Allow env override for testing
    override = os.environ.get("CLAUDE_CODE_BLOCKING_LIMIT_OVERRIDE")
    if override:
        try:
            parsed = int(override, 10)
            if parsed > 0:
                return parsed
        except ValueError:
            pass
    return max(0, effective - MANUAL_COMPACT_BUFFER_TOKENS)


# ---------------------------------------------------------------------------
# Warning level calculation
# ---------------------------------------------------------------------------

def calculate_warning_level(
    token_count: int,
    token_threshold: int,
    *,
    config: Optional[CompactWarningConfig] = None,
) -> CompactWarningLevel:
    """Determine the compaction warning level for the current token state.

    Parameters
    ----------
    token_count : int
        Current estimated token usage.  Clamped to >= 0.
    token_threshold : int
        The effective threshold for compaction (e.g. context window * 0.80).
        Ignored when ``config.token_threshold`` > 0.
    config : CompactWarningConfig or None
        GrowthBook-driven config; auto-fetched when None.

    Returns
    -------
    CompactWarningLevel
        NONE, INFO, WARN, ERROR, or BLOCK.
    """
    # Input validation
    if token_count < 0:
        logger.debug("calculate_warning_level: token_count=%d clamped to 0", token_count)
        token_count = 0

    cfg = config or _get_compact_warning_config()

    effective_threshold = (
        cfg.token_threshold if cfg.token_threshold > 0 else token_threshold
    )
    if effective_threshold <= 0:
        return CompactWarningLevel.NONE

    # Guard against zero-division (effective_threshold > 0 already checked)
    ratio = token_count / effective_threshold

    # Check levels in descending severity order
    if ratio >= cfg.block_percent:
        return CompactWarningLevel.BLOCK
    if ratio >= cfg.error_percent:
        return CompactWarningLevel.ERROR
    if ratio >= cfg.warn_percent:
        return CompactWarningLevel.WARN
    if ratio >= cfg.info_percent:
        return CompactWarningLevel.INFO
    return CompactWarningLevel.NONE


# ---------------------------------------------------------------------------
# Warning message generation
# ---------------------------------------------------------------------------

# Default message templates per warning level.
# Callers can pass a custom model name to make the message more specific.
_DEFAULT_WARNING_MESSAGES: dict[CompactWarningLevel, str] = {
    CompactWarningLevel.NONE: "",
    CompactWarningLevel.INFO: (
        "Context window filling up ({percent}% used). "
        "Consider running /compact soon."
    ),
    CompactWarningLevel.WARN: (
        "Context window is getting full ({percent}% used). "
        "Run /compact to free up space."
    ),
    CompactWarningLevel.ERROR: (
        "Context low ({percent}% remaining). "
        "Run /compact to compact & continue."
    ),
    CompactWarningLevel.BLOCK: (
        "Context critically full ({percent}% used). "
        "Compaction is required before the next message."
    ),
}


def get_warning_message(
    token_count: int,
    token_threshold: int,
    *,
    config: Optional[CompactWarningConfig] = None,
    model: str = "",
) -> str:
    """Generate a human-readable warning message for the current token state.

    Returns an empty string when no warning should be shown.
    The message includes the percentage used for context.

    Parameters
    ----------
    token_count : int
        Current estimated token usage.
    token_threshold : int
        Effective compaction threshold.
    config : CompactWarningConfig or None
        GrowthBook-driven config.
    model : str
        Optional model name for context-specific messaging.

    Returns
    -------
    str
        Human-readable warning message, or "" if NONE.
    """
    state = use_compact_warning(token_count, token_threshold, config=config)
    return _format_warning_message(state, model=model)


def get_compact_warning_message(
    token_count: int,
    token_threshold: int,
    *,
    config: Optional[CompactWarningConfig] = None,
    model: str = "",
) -> str:
    """Alias for ``get_warning_message`` — descriptive name for callers."""
    return get_warning_message(
        token_count, token_threshold, config=config, model=model
    )


def _format_warning_message(
    state: CompactWarningState,
    *,
    model: str = "",
) -> str:
    """Format a warning message from a CompactWarningState.

    Incorporates the gated/suppressed/cooldown state into the result.
    """
    if state.gated_disabled:
        return ""
    if state.suppressed and not state.on_cooldown:
        return ""
    if state.level == CompactWarningLevel.NONE:
        return ""

    percent = state.percent_used

    # Pick the base template
    template = _DEFAULT_WARNING_MESSAGES.get(
        state.level,
        "Context window at {percent}% — consider compacting."
    )

    msg = template.format(percent=percent)

    # Append cooldown note if user might wonder why they aren't seeing more warnings
    if state.on_cooldown:
        msg += " (warning on cooldown)"

    return msg


# ---------------------------------------------------------------------------
# Warning suppression helpers
# ---------------------------------------------------------------------------

def _is_on_cooldown(
    config: CompactWarningConfig,
    *,
    session_id: str = "",
) -> bool:
    """Return True when we are still inside the cooldown window.

    When *session_id* is provided, uses per-session cooldown state.
    Otherwise falls back to the module-level global.
    """
    global _last_warning_time, _session_cooldowns

    if session_id:
        last_time = _session_cooldowns.get(session_id, 0.0)
    else:
        last_time = _last_warning_time

    if last_time == 0.0:
        return False

    elapsed = time.time() - last_time
    return elapsed < config.cooldown_seconds


def _record_warning(*, session_id: str = "") -> None:
    """Record that a warning was just emitted (for cooldown tracking).

    When *session_id* is provided, updates per-session state.
    """
    global _last_warning_time, _session_cooldowns
    now = time.time()
    if session_id:
        _session_cooldowns[session_id] = now
    else:
        _last_warning_time = now


def reset_warning_cooldown(*, session_id: str = "") -> None:
    """Reset the cooldown timer so a warning can fire immediately.

    Useful in test teardown or when the user explicitly asks to see
    warnings again.  Pass *session_id* to reset only that session.
    """
    global _last_warning_time, _session_cooldowns
    if session_id:
        _session_cooldowns.pop(session_id, None)
    else:
        _last_warning_time = 0.0


def clear_all_warning_state(*, session_id: str = "") -> None:
    """Reset both cooldown and suppression state.

    This is the nuclear reset — call it at session start or after a
    major state transition (e.g. context collapse).
    """
    reset_warning_cooldown(session_id=session_id)
    clear_compact_warning_suppression()


def get_cooldown_remaining_seconds(
    config: CompactWarningConfig,
    *,
    session_id: str = "",
) -> float:
    """Return the number of seconds remaining in the cooldown window.

    Returns 0.0 when not on cooldown.
    """
    global _last_warning_time, _session_cooldowns

    if session_id:
        last_time = _session_cooldowns.get(session_id, 0.0)
    else:
        last_time = _last_warning_time

    if last_time == 0.0:
        return 0.0

    elapsed = time.time() - last_time
    remaining = config.cooldown_seconds - elapsed
    return max(0.0, remaining)


# ---------------------------------------------------------------------------
# Primary hook
# ---------------------------------------------------------------------------

@dataclass
class CompactWarningState:
    """The full compaction-warning state returned by the hook."""

    level: CompactWarningLevel = CompactWarningLevel.NONE
    token_count: int = 0
    token_threshold: int = 0
    percent_used: float = 0.0
    suppressed: bool = False
    on_cooldown: bool = False
    gated_disabled: bool = False  # True when GrowthBook kills the feature

    # ------------------------------------------------------------------
    # Convenience properties
    # ------------------------------------------------------------------

    @property
    def is_active(self) -> bool:
        """True when a warning is warranted (level != NONE)."""
        return self.level != CompactWarningLevel.NONE

    @property
    def should_display(self) -> bool:
        """True when the warning should be surfaced to the user.

        False when gated, suppressed, on cooldown, or level is NONE.
        """
        if self.gated_disabled:
            return False
        if self.suppressed:
            return False
        if self.on_cooldown:
            return False
        return self.level != CompactWarningLevel.NONE

    @property
    def is_blocking(self) -> bool:
        """True when compaction is mandatory (BLOCK level)."""
        return self.level == CompactWarningLevel.BLOCK

    @property
    def is_error(self) -> bool:
        """True at ERROR or BLOCK level."""
        return self.level in (CompactWarningLevel.ERROR, CompactWarningLevel.BLOCK)

    @property
    def tokens_remaining(self) -> int:
        """Estimated tokens remaining before the threshold."""
        return max(0, self.token_threshold - self.token_count)

    def to_dict(self) -> dict:
        """Serialize to a plain dict for transport / logging."""
        return {
            "level": self.level.value,
            "token_count": self.token_count,
            "token_threshold": self.token_threshold,
            "percent_used": self.percent_used,
            "suppressed": self.suppressed,
            "on_cooldown": self.on_cooldown,
            "gated_disabled": self.gated_disabled,
            "is_active": self.is_active,
            "should_display": self.should_display,
            "is_blocking": self.is_blocking,
            "tokens_remaining": self.tokens_remaining,
        }

    @classmethod
    def from_dict(cls, data: dict) -> CompactWarningState:
        """Deserialize from a dict (e.g. after transport)."""
        level_raw = data.get("level", "none")
        try:
            level = CompactWarningLevel(level_raw)
        except ValueError:
            level = CompactWarningLevel.NONE
        return cls(
            level=level,
            token_count=int(data.get("token_count", 0)),
            token_threshold=int(data.get("token_threshold", 0)),
            percent_used=float(data.get("percent_used", 0.0)),
            suppressed=bool(data.get("suppressed", False)),
            on_cooldown=bool(data.get("on_cooldown", False)),
            gated_disabled=bool(data.get("gated_disabled", False)),
        )


def use_compact_warning(
    token_count: int,
    token_threshold: int,
    *,
    config: Optional[CompactWarningConfig] = None,
    model: str = "",
    session_id: str = "",
) -> CompactWarningState:
    """Primary compaction-warning hook.

    Combines GrowthBook gating, warning-level calculation, suppression
    state, and cooldown to produce a single warning-state struct.

    Parameters
    ----------
    token_count : int
        Current estimated token usage.  Negative values clamped to 0.
    token_threshold : int
        Effective compaction threshold (0 = derive from model).
        When *model* is provided and *token_threshold* is 0, the
        effective context window size is used automatically.
    config : CompactWarningConfig or None
        GrowthBook-driven config; auto-fetched when None.
    model : str
        Optional model name used to auto-resolve context window when
        *token_threshold* is 0.
    session_id : str
        Optional session-scoped id for per-session cooldown tracking.

    Returns
    -------
    CompactWarningState
    """
    # Input validation
    if token_count < 0:
        token_count = 0

    cfg = config or _get_compact_warning_config()

    # GrowthBook master kill-switch
    if not cfg.enabled:
        return CompactWarningState(
            token_count=token_count,
            token_threshold=token_threshold,
            gated_disabled=True,
        )

    # Coarse boolean kill-switch (defaults open — gates are permissive)
    try:
        gate_open = check_gate_cached_or_blocking("compact_warning_enabled")
    except Exception:
        logger.debug("GrowthBook gate check failed; treating as disabled")
        gate_open = False

    if not gate_open:
        return CompactWarningState(
            token_count=token_count,
            token_threshold=token_threshold,
            gated_disabled=True,
        )

    # Resolve effective threshold: GrowthBook override → caller param →
    # model-derived context window
    if cfg.token_threshold > 0:
        effective_threshold = cfg.token_threshold
    elif token_threshold > 0:
        effective_threshold = token_threshold
    elif model:
        effective_threshold = get_effective_context_window_size(model)
    else:
        effective_threshold = 0

    # Compute percentage used
    percent = (
        (token_count / effective_threshold * 100)
        if effective_threshold > 0
        else 0.0
    )

    suppressed = is_compact_warning_suppressed() and cfg.suppressible
    on_cooldown = _is_on_cooldown(config=cfg, session_id=session_id)

    level = calculate_warning_level(
        token_count, effective_threshold, config=cfg
    )

    # If suppressed or on cooldown, downgrade to NONE for display purposes
    # (but keep the raw level accessible through the dataclass if needed)
    display_level = level
    if suppressed or on_cooldown:
        display_level = CompactWarningLevel.NONE

    return CompactWarningState(
        level=display_level,
        token_count=token_count,
        token_threshold=effective_threshold,
        percent_used=round(percent, 1),
        suppressed=suppressed,
        on_cooldown=on_cooldown,
    )


# ---------------------------------------------------------------------------
# Convenience predicates
# ---------------------------------------------------------------------------

def should_show_compact_warning(
    token_count: int,
    token_threshold: int,
    *,
    config: Optional[CompactWarningConfig] = None,
    model: str = "",
) -> bool:
    """Return True when a compaction warning should be surfaced to the user.

    This is the simplest entry point for callers that only need a
    yes/no answer.  Factors in gating, suppression, and cooldown.
    """
    state = use_compact_warning(
        token_count, token_threshold, config=config, model=model
    )
    return state.should_display


def is_at_blocking_limit(
    token_count: int,
    token_threshold: int = 0,
    *,
    config: Optional[CompactWarningConfig] = None,
    model: str = "",
) -> bool:
    """Return True when context is critically full and compaction is mandatory.

    Mirrors ``isAtBlockingLimit`` from autoCompact.ts's
    ``calculateTokenWarningState``.
    """
    state = use_compact_warning(
        token_count, token_threshold, config=config, model=model
    )
    return state.is_blocking


def should_block_for_compaction(
    token_count: int,
    token_threshold: int = 0,
    *,
    config: Optional[CompactWarningConfig] = None,
    model: str = "",
) -> bool:
    """Alias for ``is_at_blocking_limit`` — more explicit name."""
    return is_at_blocking_limit(
        token_count, token_threshold, config=config, model=model
    )


def maybe_emit_compact_warning(
    token_count: int,
    token_threshold: int,
    *,
    config: Optional[CompactWarningConfig] = None,
    model: str = "",
    session_id: str = "",
) -> CompactWarningState:
    """Evaluate and record a warning if one should be shown.

    Side effect: updates the cooldown timer when a warning is emitted
    AND the warning is actually displayable (not suppressed, not gated).

    Returns
    -------
    CompactWarningState
        The full state, including whether a warning was actually emitted.
    """
    state = use_compact_warning(
        token_count, token_threshold, config=config, model=model,
        session_id=session_id,
    )
    if state.should_display:
        _record_warning(session_id=session_id)
    return state


# ---------------------------------------------------------------------------
# Warning summary (comprehensive one-shot API)
# ---------------------------------------------------------------------------

def compute_warning_summary(
    token_count: int,
    token_threshold: int = 0,
    *,
    config: Optional[CompactWarningConfig] = None,
    model: str = "",
    session_id: str = "",
) -> dict:
    """Compute a comprehensive warning summary in a single call.

    Returns a dict with all warning information, suitable for transport
    to a frontend or for logging.

    Returns
    -------
    dict
        Keys: level, message, percent_used, is_active, should_display,
        is_blocking, token_count, token_threshold, tokens_remaining,
        suppressed, on_cooldown, gated_disabled, cooldown_remaining_seconds.
    """
    cfg = config or _get_compact_warning_config()
    state = use_compact_warning(
        token_count, token_threshold, config=cfg, model=model,
        session_id=session_id,
    )
    cooldown_remaining = get_cooldown_remaining_seconds(
        cfg, session_id=session_id
    )

    return {
        "level": state.level.value,
        "message": _format_warning_message(state, model=model),
        "percent_used": state.percent_used,
        "is_active": state.is_active,
        "should_display": state.should_display,
        "is_blocking": state.is_blocking,
        "token_count": state.token_count,
        "token_threshold": state.token_threshold,
        "tokens_remaining": state.tokens_remaining,
        "suppressed": state.suppressed,
        "on_cooldown": state.on_cooldown,
        "gated_disabled": state.gated_disabled,
        "cooldown_remaining_seconds": round(cooldown_remaining, 1),
    }


# ---------------------------------------------------------------------------
# Threshold resolution helpers
# ---------------------------------------------------------------------------

def get_effective_threshold(
    token_threshold: int = 0,
    *,
    config: Optional[CompactWarningConfig] = None,
    model: str = "",
) -> int:
    """Resolve the effective token threshold using the full resolution chain.

    Priority: GrowthBook config.token_threshold > caller token_threshold >
    model-derived context window.

    Returns 0 when no threshold can be determined.
    """
    cfg = config or _get_compact_warning_config()
    return _get_effective_threshold(cfg, token_threshold) or (
        get_effective_context_window_size(model) if model and token_threshold <= 0 else 0
    )


# ---------------------------------------------------------------------------
# GrowthBook helper: refresh remote config
# ---------------------------------------------------------------------------

def refresh_compact_warning_config_from_remote(
    remote_config: dict,
) -> None:
    """Cache a remotely-fetched compact-warning config in GrowthBook.

    Call this after receiving updated feature flags from the server so
    that subsequent calls to the hook see fresh values.

    Also validates the incoming config and logs warnings for bad values.
    """
    if not isinstance(remote_config, dict):
        logger.warning(
            "refresh_compact_warning_config_from_remote: expected dict, got %s",
            type(remote_config).__name__,
        )
        return

    # Validate before caching
    try:
        cfg = CompactWarningConfig(
            enabled=bool(remote_config.get("enabled", True)),
            suppressible=bool(remote_config.get("suppressible", True)),
            cooldown_seconds=int(remote_config.get("cooldownSeconds", 300)),
            token_threshold=int(remote_config.get("tokenThreshold", 0)),
            warn_percent=float(remote_config.get("warnPercent", 0.75)),
            error_percent=float(remote_config.get("errorPercent", 0.90)),
            info_percent=float(remote_config.get("infoPercent", 0.60)),
            block_percent=float(remote_config.get("blockPercent", 0.97)),
        )
        validation_errors = cfg.validate()
        if validation_errors:
            logger.warning(
                "Remote compact_warning config has validation issues: %s",
                "; ".join(validation_errors),
            )
    except (ValueError, TypeError) as e:
        logger.warning(
            "Remote compact_warning config failed to parse: %s — caching raw dict",
            e,
        )

    try:
        set_cached_feature_value("compact_warning", remote_config)
    except Exception as e:
        logger.error("Failed to cache remote compact_warning config: %s", e)


# ---------------------------------------------------------------------------
# Legacy-compatible aliases (match original TS export names)
# ---------------------------------------------------------------------------

use_compact_warning_suppression = is_compact_warning_suppressed


def check_compact_warning_gate() -> bool:
    """Check whether the compact_warning_enabled gate is open.

    Named function (not lambda) for better tracebacks and debuggability.
    """
    try:
        return is_feature_enabled("compact_warning_enabled")
    except Exception:
        return False
