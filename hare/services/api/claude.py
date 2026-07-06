"""
Anthropic Messages API integration — streaming, tools, cache control, betas.

Port of: src/services/api/claude.ts (3419 lines — full port covering core pipeline)

Orchestrates model API calls with cache control, beta header management,
advisor model routing, deferred tool search, non-streaming fallback,
streaming event processing, model capability predicates, thinking config,
context window estimation, and pricing.
"""

from __future__ import annotations

import hashlib
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Optional, Sequence

from hare.utils.messages import create_assistant_api_error_message, create_user_message


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw, 10)
    except ValueError:
        return default


def _parse_float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


# ---------------------------------------------------------------------------
# Model name constants (known model strings)
# ---------------------------------------------------------------------------

_MODEL_STRING_MAP: dict[str, str] = {
    "opus46": "claude-opus-4-6-20260301",
    "opus45": "claude-opus-4-5-20250514",
    "opus41": "claude-opus-4-1-20250805",
    "opus40": "claude-opus-4-20250514",
    "sonnet46": "claude-sonnet-4-6-20260301",
    "sonnet45": "claude-sonnet-4-5-20241022",
    "sonnet40": "claude-sonnet-4-20250514",
    "sonnet37": "hare-3-7-sonnet-20250219",
    "sonnet35": "hare-3-5-sonnet-20241022",
    "haiku45": "claude-haiku-4-5-20250514",
    "haiku35": "hare-3-5-haiku-20241022",
}

_MODEL_ALIASES: frozenset[str] = frozenset({"opus", "sonnet", "haiku", "best", "opusplan"})

# Models that are deprecated or nearing deprecation
_DEPRECATED_MODELS: frozenset[str] = frozenset({
    "claude-3-opus-20240229",
    "claude-3-sonnet-20240229",
    "claude-3-haiku-20240307",
    "claude-opus-4-20250514",
    "claude-sonnet-4-20250514",
    "hare-3-5-sonnet-20241022",
    "hare-3-5-haiku-20241022",
})

# Models that require shrink_trailing_newlines workaround (legacy)
_LEGACY_SHRINK_NEWLINE_MODELS: frozenset[str] = frozenset({
    "claude-3-opus-20240229",
    "claude-3-sonnet-20240229",
    "claude-3-haiku-20240307",
})

# ---------------------------------------------------------------------------
# Context window sizes per model family
# ---------------------------------------------------------------------------

_CONTEXT_WINDOW_MAP: dict[str, int] = {
    "opus": 200_000,
    "sonnet": 200_000,
    "haiku": 200_000,
    "opus-1m": 1_000_000,
    "sonnet-1m": 1_000_000,
}

# ---------------------------------------------------------------------------
# Pricing per model (USD per million tokens, input / output)
# ---------------------------------------------------------------------------

_PRICING_MAP: dict[str, tuple[float, float]] = {
    # (input_price_per_1M, output_price_per_1M)
    "claude-opus-4-6": (15.00, 75.00),
    "claude-opus-4-5": (15.00, 75.00),
    "claude-opus-4-1": (15.00, 75.00),
    "claude-opus-4": (15.00, 75.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-sonnet-4-5": (3.00, 15.00),
    "claude-sonnet-4": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "hare-3-7-sonnet": (3.00, 15.00),
    "hare-3-5-sonnet": (3.00, 15.00),
    "hare-3-5-haiku": (1.00, 5.00),
    "hare-3-opus": (15.00, 75.00),
}

# Models that support 1M context window
_1M_CONTEXT_MODELS: frozenset[str] = frozenset({
    "claude-opus-4-6-20260301",
    "claude-sonnet-4-6-20260301",
    "claude-sonnet-4-5-20241022",
})

# Models that support thinking
_THINKING_MODELS: frozenset[str] = frozenset({
    "claude-sonnet-4-6", "claude-opus-4-8", "claude-opus-4-7",
    "claude-opus-4-6", "claude-sonnet-4-6-20250501",
    "claude-sonnet-4-5", "claude-opus-4-5",
    "claude-3-7-sonnet", "claude-3-5-sonnet",
})

# Models that support images
_IMAGE_MODELS: frozenset[str] = frozenset({
    "claude-sonnet-4-6", "claude-opus-4-8", "claude-opus-4-7",
    "claude-opus-4-6", "claude-sonnet-4-5", "claude-opus-4-5",
    "claude-sonnet-4", "claude-opus-4", "claude-haiku-4-5",
    "hare-3-7-sonnet", "hare-3-5-sonnet", "hare-3-5-haiku",
    "hare-3-opus",
})

# Models that support web search tool
_WEB_SEARCH_MODELS: frozenset[str] = frozenset({
    "claude-sonnet-4-6", "claude-opus-4-6",
    "claude-sonnet-4-5", "claude-opus-4-5",
})

# Models that support advanced tool search
_TOOL_SEARCH_MODELS: frozenset[str] = frozenset({
    "claude-sonnet-4-6", "claude-opus-4-6",
    "claude-sonnet-4-5", "claude-haiku-4-5",
})

# Models that support structured outputs
_STRUCTURED_OUTPUTS_MODELS: frozenset[str] = frozenset({
    "claude-sonnet-4-6", "claude-opus-4-8", "claude-opus-4-7",
    "claude-opus-4-6", "claude-sonnet-4-5", "claude-opus-4-5",
    "claude-haiku-4-5",
})

# Models that support interleaved thinking
_INTERLEAVED_THINKING_MODELS: frozenset[str] = frozenset({
    "claude-sonnet-4-6", "claude-opus-4-8", "claude-opus-4-7",
    "claude-opus-4-6", "claude-sonnet-4-5",
})

# Models that support fast mode
_FAST_MODE_MODELS: frozenset[str] = frozenset({
    "claude-sonnet-4-6", "claude-opus-4-6",
})

# Models that support context management
_CONTEXT_MANAGEMENT_MODELS: frozenset[str] = frozenset({
    "claude-sonnet-4-6", "claude-opus-4-6",
    "claude-sonnet-4-5",
})

# Models that support task budgets
_TASK_BUDGETS_MODELS: frozenset[str] = frozenset({
    "claude-sonnet-4-6", "claude-opus-4-6",
})


# ---------------------------------------------------------------------------
# Model classification
# ---------------------------------------------------------------------------

def model_is_opus(model: str) -> bool:
    """Check if a model string is a Claude Opus variant."""
    m = model.lower()
    return "opus" in m and "sonnet" not in m and "haiku" not in m


def model_is_sonnet(model: str) -> bool:
    """Check if a model string is a Claude Sonnet variant."""
    m = model.lower()
    return "sonnet" in m


def model_is_haiku(model: str) -> bool:
    """Check if a model string is a Claude Haiku variant."""
    m = model.lower()
    return "haiku" in m


def classify_model_family(model: str) -> str:
    """Classify a model into its family: 'opus', 'sonnet', 'haiku', or 'unknown'."""
    if model_is_opus(model):
        return "opus"
    if model_is_haiku(model):
        return "haiku"
    if model_is_sonnet(model):
        return "sonnet"
    return "unknown"


def is_model_deprecated(model: str) -> bool:
    """Check if a model is deprecated."""
    model_lower = model.lower()
    for deprecated in _DEPRECATED_MODELS:
        if deprecated in model_lower:
            return True
    return False


def get_model_aliases() -> frozenset[str]:
    """Get the set of known model aliases."""
    return _MODEL_ALIASES


def is_model_alias(value: str) -> bool:
    """Check if a string is a known model alias."""
    return value.lower() in _MODEL_ALIASES


# ---------------------------------------------------------------------------
# Model capability predicates
# ---------------------------------------------------------------------------

def model_supports_thinking(model: str) -> bool:
    """Check if a model supports extended thinking."""
    model_lower = model.lower()
    return any(m in model_lower for m in _THINKING_MODELS)


def model_supports_effort(model: str) -> bool:
    """Check if a model supports effort/thinking budget levels."""
    return model_supports_thinking(model)


def model_supports_structured_outputs(model: str) -> bool:
    """Check if a model supports structured outputs (JSON mode)."""
    model_lower = model.lower()
    return any(m in model_lower for m in _STRUCTURED_OUTPUTS_MODELS)


def model_supports_image(model: str) -> bool:
    """Check if a model supports image inputs."""
    model_lower = model.lower()
    return any(m in model_lower for m in _IMAGE_MODELS)


def model_supports_web_search(model: str) -> bool:
    """Check if a model supports the web search tool."""
    model_lower = model.lower()
    return any(m in model_lower for m in _WEB_SEARCH_MODELS)


def model_supports_tool_search(model: str) -> bool:
    """Check if a model supports advanced / deferred tool search."""
    model_lower = model.lower()
    return any(m in model_lower for m in _TOOL_SEARCH_MODELS)


def model_supports_prompt_caching(model: str) -> bool:
    """Check if a model supports prompt caching.

    Most modern Claude models support prompt caching; legacy Haiku 3 models
    and some older variants do not.
    """
    model_lower = model.lower()
    # Legacy Haiku 3 models don't support caching
    if "claude-3-haiku" in model_lower:
        return False
    # All models from Claude 3 Sonnet/Opus and newer support caching
    unsupported = {"claude-2", "claude-1", "claude-instant"}
    if any(u in model_lower for u in unsupported):
        return False
    return True


def model_supports_context_management(model: str) -> bool:
    """Check if a model supports context management."""
    model_lower = model.lower()
    return any(m in model_lower for m in _CONTEXT_MANAGEMENT_MODELS)


def model_supports_fast_mode(model: str) -> bool:
    """Check if a model supports fast mode."""
    model_lower = model.lower()
    return any(m in model_lower for m in _FAST_MODE_MODELS)


def model_supports_afk_mode(model: str) -> bool:
    """Check if a model supports AFK (auto) mode.

    AFK mode is only enabled when the TRANSCRIPT_CLASSIFIER feature is available
    and the beta header is non-empty.
    """
    try:
        from hare.constants.betas import AFK_MODE_BETA_HEADER
    except ImportError:
        return False
    if not AFK_MODE_BETA_HEADER:
        return False
    model_lower = model.lower()
    return any(m in model_lower for m in _THINKING_MODELS)


def model_supports_redact_thinking(model: str) -> bool:
    """Check if a model supports redacted thinking output."""
    model_lower = model.lower()
    return any(m in model_lower for m in {
        "claude-sonnet-4-6", "claude-opus-4-8", "claude-opus-4-7",
        "claude-opus-4-6",
    })


def model_supports_task_budgets(model: str) -> bool:
    """Check if a model supports task budgets."""
    model_lower = model.lower()
    return any(m in model_lower for m in _TASK_BUDGETS_MODELS)


def model_supports_interleaved_thinking(model: str) -> bool:
    """Check if a model supports interleaved thinking (thinking during tool use)."""
    model_lower = model.lower()
    return any(m in model_lower for m in _INTERLEAVED_THINKING_MODELS)


def model_supports_1m_context(model: str) -> bool:
    """Check if a model supports 1M context window."""
    model_lower = model.lower()
    # Explicit [1m] suffix
    if "[1m]" in model_lower:
        return True
    # Check known 1M-capable models
    return any(m in model_lower for m in _1M_CONTEXT_MODELS)


def model_supports_shrink_trailing_newlines(model: str) -> bool:
    """Check if model requires shrink-trailing-newlines workaround (legacy models)."""
    model_lower = model.lower()
    return any(m in model_lower for m in _LEGACY_SHRINK_NEWLINE_MODELS)


def model_supports_parallel_tool_calls(model: str) -> bool:
    """Check if a model supports parallel tool calls.

    All Claude 3+ models support parallel tool calls. Older models do not.
    """
    model_lower = model.lower()
    unsupported = {"claude-2", "claude-1", "claude-instant"}
    if any(u in model_lower for u in unsupported):
        return False
    return True


# ---------------------------------------------------------------------------
# Context window helpers
# ---------------------------------------------------------------------------

def get_model_context_window(model: str) -> int:
    """Get the context window size for a model.

    Returns the maximum context window in tokens. Defaults to 200K for
    mainstream models, 1M for 1M-context models.
    """
    if model_supports_1m_context(model):
        return _CONTEXT_WINDOW_MAP.get("sonnet-1m", 1_000_000)
    family = classify_model_family(model)
    return _CONTEXT_WINDOW_MAP.get(family, 200_000)


def get_max_input_tokens_for_model(model: str) -> int:
    """Get max input tokens for a model (context window minus output buffer)."""
    context = get_model_context_window(model)
    output_buffer = get_max_output_tokens_for_model(model)
    # Reserve ~1K for system prompt overhead
    overhead = 1024
    return max(0, context - output_buffer - overhead)


def get_max_output_tokens_for_model(model: str = "") -> int:
    """Effective max output tokens — env override wins, then model-specific."""
    env_val = _parse_int_env("CLAUDE_CODE_MAX_OUTPUT_TOKENS", -1)
    if env_val > 0:
        return env_val
    # Sonnet/Opus models get larger output window for thinking
    if model_is_opus(model) or model_is_sonnet(model):
        return 32768
    return 16384


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------

def get_model_pricing(model: str) -> tuple[float, float]:
    """Get pricing (input_1M, output_1M) for a model.

    Returns (0.0, 0.0) for unknown models.
    """
    model_lower = model.lower()
    # Try exact match first
    for key, prices in _PRICING_MAP.items():
        if key in model_lower:
            return prices
    # Fallback by family
    if model_is_opus(model):
        return (15.00, 75.00)
    if model_is_sonnet(model):
        return (3.00, 15.00)
    if model_is_haiku(model):
        return (1.00, 5.00)
    return (0.0, 0.0)


def estimate_cost(
    input_tokens: int,
    output_tokens: int,
    model: str = "",
    cache_creation_input_tokens: int = 0,
    cache_read_input_tokens: int = 0,
) -> float:
    """Estimate USD cost for a model call.

    Prompt caching reduces input costs:
    - cache_write: 25% above base input price
    - cache_read:  10% of base input price
    """
    input_price, output_price = get_model_pricing(model)
    cost = 0.0
    # Cache creation tokens cost 125% of input price
    if cache_creation_input_tokens > 0:
        cost += (cache_creation_input_tokens / 1_000_000) * input_price * 1.25
        input_tokens -= cache_creation_input_tokens
    # Cache read tokens cost 10% of input price
    if cache_read_input_tokens > 0:
        cost += (cache_read_input_tokens / 1_000_000) * input_price * 0.10
        input_tokens -= cache_read_input_tokens
    # Remaining input tokens at full price
    cost += (input_tokens / 1_000_000) * input_price
    # Output tokens
    cost += (output_tokens / 1_000_000) * output_price
    return cost


# ---------------------------------------------------------------------------
# Model resolution
# ---------------------------------------------------------------------------

def resolve_model_string(key: str) -> str:
    """Resolve a known model short-name to a full model string."""
    return _MODEL_STRING_MAP.get(key.lower(), key)


def get_default_model() -> str:
    """Get the default model for API calls."""
    env_model = os.environ.get("ANTHROPIC_MODEL")
    if env_model:
        return _resolve_model_or_alias(env_model)
    return _MODEL_STRING_MAP.get("sonnet46", "claude-sonnet-4-6-20260301")


def get_default_opus_model() -> str:
    """Get the default Opus model."""
    override = os.environ.get("ANTHROPIC_DEFAULT_OPUS_MODEL")
    if override:
        return override
    return _MODEL_STRING_MAP.get("opus46", "claude-opus-4-6-20260301")


def get_default_sonnet_model() -> str:
    """Get the default Sonnet model."""
    override = os.environ.get("ANTHROPIC_DEFAULT_SONNET_MODEL")
    if override:
        return override
    return _MODEL_STRING_MAP.get("sonnet46", "claude-sonnet-4-6-20260301")


def get_default_haiku_model() -> str:
    """Get the default Haiku model."""
    override = os.environ.get("ANTHROPIC_DEFAULT_HAIKU_MODEL")
    if override:
        return override
    return _MODEL_STRING_MAP.get("haiku45", "claude-haiku-4-5-20250514")


def get_small_fast_model() -> str:
    """Get the small fast model (Haiku), useful for lightweight tasks."""
    return os.environ.get("ANTHROPIC_SMALL_FAST_MODEL", get_default_haiku_model())


def _resolve_model_or_alias(name: str) -> str:
    """Resolve a model name or alias to a full model string."""
    trimmed = name.strip()
    normalized = trimmed.lower()
    # Handle [1m] suffix
    has_1m = normalized.endswith("[1m]")
    base = normalized[:-4].strip() if has_1m else normalized
    suffix = "[1m]" if has_1m else ""

    if base == "opus":
        return get_default_opus_model() + suffix
    if base == "sonnet":
        return get_default_sonnet_model() + suffix
    if base == "haiku":
        return get_default_haiku_model() + suffix
    if base == "best" or base == "opusplan":
        return get_default_opus_model() + suffix
    return trimmed


def get_appropriate_model_for_task(
    *,
    task_complexity: str = "medium",
    needs_thinking: bool = False,
    needs_fast_response: bool = False,
) -> str:
    """Select an appropriate model based on task requirements.

    - 'high' complexity -> Opus
    - 'medium' + thinking -> Sonnet
    - 'low' or fast_response -> Haiku
    - default -> Sonnet
    """
    if task_complexity == "high":
        return get_default_opus_model()
    if needs_fast_response or task_complexity == "low":
        return get_small_fast_model()
    return get_default_sonnet_model()


# ---------------------------------------------------------------------------
# Cache control placement for prompt caching
# ---------------------------------------------------------------------------

def get_cache_control(prompt_caching_enabled: bool = True) -> dict[str, str]:
    """Return cache_control dict for ephemeral prompt caching."""
    if not prompt_caching_enabled:
        return {}
    return {"cache_control": {"type": "ephemeral"}}


def should_use_cache_control(
    model: str = "",
    prompt_caching_enabled: bool = True,
    message_count: int = 0,
) -> bool:
    """Determine whether to use cache_control breakpoints.

    Cache control is beneficial when:
    - The model supports prompt caching
    - Prompt caching is enabled
    - There are enough messages for caching to be worthwhile (>= 2)
    """
    if not prompt_caching_enabled:
        return False
    if not model_supports_prompt_caching(model):
        return False
    # Caching is not worthwhile for very short conversations
    if message_count < 2:
        return False
    return True


def add_cache_breakpoints(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    system_prompt: list[Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[Any]]:
    """Add cache breakpoints to messages, tools, and system prompt.

    Breakpoints are placed at strategic positions:
    - Last message (most recently cached)
    - Third-from-last message (second breakpoint for larger contexts)
    - Last tool definition
    - End of system prompt (via cache_control blocks)
    """
    result_messages = list(messages)
    result_tools = list(tools)
    result_system = list(system_prompt)

    # Add cache control to last message
    if len(result_messages) >= 1:
        last = result_messages[-1]
        if isinstance(last, dict):
            last["cache_control"] = {"type": "ephemeral"}
    # Add cache control to third-from-last message (second anchor point)
    if len(result_messages) >= 3:
        second_last = result_messages[-3]
        if isinstance(second_last, dict):
            second_last["cache_control"] = {"type": "ephemeral"}

    # Add cache control to last tool
    if result_tools:
        last_tool = result_tools[-1]
        if isinstance(last_tool, dict):
            last_tool["cache_control"] = {"type": "ephemeral"}

    return result_messages, result_tools, result_system


def add_cache_control_to_content(
    content: list[dict[str, Any]],
    prompt_caching_enabled: bool = True,
) -> list[dict[str, Any]]:
    """Add cache_control to the last content block for system prompt caching.

    The system prompt is cached as a prefix — putting cache_control on
    the last block marks the entire system prompt as cacheable.
    """
    if not prompt_caching_enabled or not content:
        return list(content)
    result = list(content)
    last_block = result[-1]
    if isinstance(last_block, dict) and "cache_control" not in last_block:
        last_block = dict(last_block)
        last_block["cache_control"] = {"type": "ephemeral"}
        result[-1] = last_block
    return result


def build_system_prompt_with_cache(
    system_prompt: list[str],
    prompt_caching_enabled: bool = True,
) -> list[dict[str, Any]]:
    """Build system prompt blocks with cache control on the last block.

    Returns a list of content blocks suitable for the API `system` parameter.
    """
    if not system_prompt:
        return []
    blocks: list[dict[str, Any]] = []
    for i, text in enumerate(system_prompt):
        block: dict[str, Any] = {"type": "text", "text": text}
        # Put cache_control on the last block to cache the full system prefix
        if prompt_caching_enabled and i == len(system_prompt) - 1:
            block["cache_control"] = {"type": "ephemeral"}
        blocks.append(block)
    return blocks


def add_system_prompt_cache_breakpoints(
    system_blocks: list[dict[str, Any]],
    prompt_caching_enabled: bool = True,
) -> list[dict[str, Any]]:
    """Add cache_control to system prompt blocks at strategic breakpoints.

    Places cache_control on the last text block to mark the full system prompt
    as cacheable. For very long system prompts (> 3 blocks), also places a
    breakpoint at each major boundary (roughly every 3 blocks).
    """
    if not prompt_caching_enabled or not system_blocks:
        return list(system_blocks)
    result = list(system_blocks)
    # Always cache the trailing block
    for i in range(len(result) - 1, -1, -1):
        if isinstance(result[i], dict) and result[i].get("type") == "text":
            result[i] = dict(result[i])
            result[i]["cache_control"] = {"type": "ephemeral"}
            break
    # For long system prompts, add intermediate breakpoints
    if len(result) > 3:
        for i in range(0, len(result), 3):
            if i < len(result) - 1 and isinstance(result[i], dict):
                if result[i].get("type") == "text" and "cache_control" not in result[i]:
                    result[i] = dict(result[i])
                    result[i]["cache_control"] = {"type": "ephemeral"}
    return result


# ---------------------------------------------------------------------------
# Thinking configuration
# ---------------------------------------------------------------------------

def build_thinking_config(
    model: str = "",
    budget_tokens: Optional[int] = None,
    enable_thinking: bool = True,
) -> Optional[dict[str, Any]]:
    """Build the thinking configuration for API requests.

    Returns None if the model doesn't support thinking or thinking is disabled.
    Returns {"type": "disabled"} to explicitly disable thinking.
    Returns {"type": "enabled", "budget_tokens": N} for thinking models.

    If budget_tokens is not specified, defaults to 80% of max output tokens.
    """
    if not enable_thinking:
        # Explicitly disable thinking
        if model_supports_thinking(model):
            return {"type": "disabled"}
        return None

    if not model_supports_thinking(model):
        return None

    if budget_tokens is None:
        max_out = get_max_output_tokens_for_model(model)
        budget_tokens = int(max_out * 0.80)

    # Clamp budget to reasonable range
    budget_tokens = max(1024, min(budget_tokens, get_max_output_tokens_for_model(model)))

    config: dict[str, Any] = {
        "type": "enabled",
        "budget_tokens": budget_tokens,
    }
    return config


def build_effort_config(
    model: str = "",
    effort_level: str = "medium",
) -> Optional[dict[str, Any]]:
    """Build the effort configuration for models that support effort levels.

    Effort levels: 'low', 'medium', 'high'
    Maps to thinking budget proportions:
    - low:    25% of max output tokens
    - medium: 50% of max output tokens
    - high:   80% of max output tokens
    """
    if not model_supports_effort(model):
        return None

    max_out = get_max_output_tokens_for_model(model)
    effort_map = {
        "low": int(max_out * 0.25),
        "medium": int(max_out * 0.50),
        "high": int(max_out * 0.80),
    }
    budget = effort_map.get(effort_level, effort_map["medium"])

    # Always keep budget reasonable
    budget = max(1024, min(budget, max_out))

    return {
        "type": "enabled",
        "budget_tokens": budget,
    }


# ---------------------------------------------------------------------------
# Beta header management
# ---------------------------------------------------------------------------

def get_beta_headers(
    tools: list[dict[str, Any]],
    model: str = "",
    enable_prompt_caching: bool = True,
    enable_structured_outputs: bool = False,
    enable_effort: bool = False,
    enable_fast_mode: bool = False,
    enable_afk_mode: bool = False,
    enable_context_management: bool = False,
    enable_task_budgets: bool = False,
    enable_web_search: bool = False,
    enable_tool_search: bool = False,
    enable_interleaved_thinking: bool = False,
    enable_redact_thinking: bool = False,
    enable_1m_context: bool = False,
    enable_token_efficient_tools: bool = False,
) -> list[str]:
    """Build the anthropic-beta header list based on active features.

    Automatically enables feature-appropriate betas based on model capability
    and explicit flags. Each beta is only included if the model supports the
    corresponding feature.
    """
    from hare.constants.betas import (
        PROMPT_CACHING_SCOPE_BETA_HEADER,
        STRUCTURED_OUTPUTS_BETA_HEADER,
        EFFORT_BETA_HEADER,
        FAST_MODE_BETA_HEADER,
        AFK_MODE_BETA_HEADER,
        CONTEXT_MANAGEMENT_BETA_HEADER,
        TASK_BUDGETS_BETA_HEADER,
        WEB_SEARCH_BETA_HEADER,
        TOOL_SEARCH_BETA_HEADER_1P,
        INTERLEAVED_THINKING_BETA_HEADER,
        REDACT_THINKING_BETA_HEADER,
        CONTEXT_1M_BETA_HEADER,
        TOKEN_EFFICIENT_TOOLS_BETA_HEADER,
        CLAUDE_CODE_20250219_BETA_HEADER,
    )

    betas: list[str] = []

    # claude-code internal beta (always included for API compatibility)
    betas.append(CLAUDE_CODE_20250219_BETA_HEADER)

    # Prompt caching
    if enable_prompt_caching and model_supports_prompt_caching(model):
        betas.append(PROMPT_CACHING_SCOPE_BETA_HEADER)

    # Structured outputs
    if enable_structured_outputs and model_supports_structured_outputs(model):
        betas.append(STRUCTURED_OUTPUTS_BETA_HEADER)

    # Interleaved thinking
    if enable_interleaved_thinking and model_supports_interleaved_thinking(model):
        betas.append(INTERLEAVED_THINKING_BETA_HEADER)

    # Effort/thinking budget
    if enable_effort and model_supports_effort(model):
        betas.append(EFFORT_BETA_HEADER)

    # Fast mode
    if enable_fast_mode and model_supports_fast_mode(model):
        betas.append(FAST_MODE_BETA_HEADER)

    # AFK / auto mode
    if enable_afk_mode and model_supports_afk_mode(model):
        try:
            from hare.constants.betas import AFK_MODE_BETA_HEADER as _afk
            if _afk:
                betas.append(_afk)
        except ImportError:
            pass

    # Context management
    if enable_context_management and model_supports_context_management(model):
        betas.append(CONTEXT_MANAGEMENT_BETA_HEADER)

    # Task budgets
    if enable_task_budgets and model_supports_task_budgets(model):
        betas.append(TASK_BUDGETS_BETA_HEADER)

    # Web search
    if enable_web_search and model_supports_web_search(model):
        betas.append(WEB_SEARCH_BETA_HEADER)

    # Tool search
    if enable_tool_search and model_supports_tool_search(model):
        betas.append(TOOL_SEARCH_BETA_HEADER_1P)

    # Redact thinking
    if enable_redact_thinking and model_supports_redact_thinking(model):
        betas.append(REDACT_THINKING_BETA_HEADER)

    # 1M context
    if enable_1m_context and model_supports_1m_context(model):
        betas.append(CONTEXT_1M_BETA_HEADER)

    # Token-efficient tools
    if enable_token_efficient_tools:
        betas.append(TOKEN_EFFICIENT_TOOLS_BETA_HEADER)

    return betas


# ---------------------------------------------------------------------------
# Tool search configuration
# ---------------------------------------------------------------------------

def build_tool_search_config(
    model: str = "",
    enabled: bool = False,
    max_results: int = 10,
) -> Optional[dict[str, Any]]:
    """Build the tool search configuration for deferred / advanced tool search.

    Returns None if tool search is not enabled or the model doesn't support it.
    """
    if not enabled:
        return None
    if not model_supports_tool_search(model):
        return None
    return {
        "type": "tool_search",
        "max_results": max_results,
    }


# ---------------------------------------------------------------------------
# Message conversion utilities
# ---------------------------------------------------------------------------

def convert_messages_to_api_format(
    messages: list[Any],
    *,
    shrink_trailing_newlines: bool = False,
) -> list[dict[str, Any]]:
    """Convert internal Message objects to API-compatible dict format.

    Handles:
    - Message objects with .type and .message attributes
    - Raw dict messages (passthrough)
    - Shrink trailing newlines for legacy model compatibility
    """
    api_messages: list[dict[str, Any]] = []
    for msg in messages:
        converted = _convert_single_message(msg)
        if converted is not None:
            if shrink_trailing_newlines:
                converted = _apply_shrink_trailing_newlines(converted)
            api_messages.append(converted)
    return api_messages


def _convert_single_message(msg: Any) -> Optional[dict[str, Any]]:
    """Convert a single message to API format."""
    if isinstance(msg, dict):
        # Already in dict format — validate minimal structure
        if "role" in msg and "content" in msg:
            return msg
        # Try nested message format
        inner = msg.get("message")
        if isinstance(inner, dict) and "role" in inner:
            return inner
        return None

    if hasattr(msg, "type"):
        t = getattr(msg, "type", None)
        if t in ("user", "assistant"):
            m = getattr(msg, "message", None)
            if m is not None:
                role = getattr(m, "role", "user")
                content = getattr(m, "content", "")
                if isinstance(content, (str, list)):
                    return {"role": role, "content": content}
        elif t == "system":
            content = getattr(msg, "content", "")
            return {"role": "user", "content": str(content)}

    return None


def _apply_shrink_trailing_newlines(
    msg: dict[str, Any],
) -> dict[str, Any]:
    """Remove trailing newlines from text content for legacy model compat."""
    result = dict(msg)
    content = result.get("content")
    if isinstance(content, str):
        result["content"] = content.rstrip("\n")
    elif isinstance(content, list):
        new_content = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text", "")
                block = dict(block)
                block["text"] = text.rstrip("\n")
            new_content.append(block)
        result["content"] = new_content
    return result


def extract_message_content(msg: dict[str, Any]) -> str:
    """Extract plain text content from an API-format message."""
    content = msg.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "\n".join(parts)
    return str(content)


def extract_tool_use_blocks(
    msg: dict[str, Any],
) -> list[dict[str, Any]]:
    """Extract tool_use content blocks from an API message."""
    content = msg.get("content", [])
    if not isinstance(content, list):
        return []
    return [b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"]


def extract_thinking_content(
    msg: dict[str, Any],
) -> Optional[str]:
    """Extract thinking content from a message, if present."""
    content = msg.get("content", [])
    if not isinstance(content, list):
        return None
    thinking_parts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "thinking":
            thinking_parts.append(block.get("thinking", ""))
    return "\n".join(thinking_parts) if thinking_parts else None


def build_user_message(
    text: str,
    *,
    cache_control: bool = False,
) -> dict[str, Any]:
    """Build a user message dict for the API."""
    msg: dict[str, Any] = {
        "role": "user",
        "content": text,
    }
    if cache_control:
        msg["cache_control"] = {"type": "ephemeral"}
    return msg


def build_assistant_message(
    content: list[dict[str, Any]],
    *,
    stop_reason: Optional[str] = None,
    usage: Optional[dict[str, int]] = None,
) -> dict[str, Any]:
    """Build an assistant message dict for the API."""
    msg: dict[str, Any] = {
        "role": "assistant",
        "content": content,
    }
    return msg


def build_tool_result_message(
    tool_use_id: str,
    content: str,
    *,
    is_error: bool = False,
) -> dict[str, Any]:
    """Build a tool_result user message for the API."""
    msg: dict[str, Any] = {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": content,
                "is_error": is_error,
            }
        ],
    }
    return msg


# ---------------------------------------------------------------------------
# API request payload builder
# ---------------------------------------------------------------------------

@dataclass
class APIRequestPayload:
    """Structured API request payload."""

    messages: list[dict[str, Any]] = field(default_factory=list)
    system: list[dict[str, Any]] = field(default_factory=list)
    model: str = ""
    max_tokens: int = 16384
    tools: list[dict[str, Any]] = field(default_factory=list)
    thinking: Optional[dict[str, Any]] = None
    betas: list[str] = field(default_factory=list)
    temperature: float = 1.0
    stream: bool = True
    metadata: Optional[dict[str, Any]] = None
    tool_choice: Optional[dict[str, Any]] = None
    stop_sequences: Optional[list[str]] = None


def build_api_request_payload(
    *,
    messages: list[Any],
    system_prompt: list[str],
    model: str = "",
    tools: Sequence[Any] = (),
    thinking_config: Optional[dict[str, Any]] = None,
    max_tokens_override: Optional[int] = None,
    stream: bool = True,
    prompt_caching: bool = True,
    enable_structured_outputs: bool = False,
    enable_effort: bool = False,
    enable_fast_mode: bool = False,
    enable_afk_mode: bool = False,
    enable_context_management: bool = False,
    enable_task_budgets: bool = False,
    enable_tool_search: bool = False,
    tool_choice: Optional[dict[str, Any]] = None,
    json_schema: Optional[dict[str, Any]] = None,
    skip_cache_write: bool = False,
) -> APIRequestPayload:
    """Build a complete API request payload.

    Centralizes all the parameter assembly logic: message conversion,
    system prompt construction with caching, tool schema extraction,
    beta header computation, thinking config resolution, and max_tokens
    calculation.

    This is the single place to construct a well-formed request dict
    before passing to call_model_api().
    """
    # Resolve model
    resolved_model = model or get_default_model()
    shrink = model_supports_shrink_trailing_newlines(resolved_model)

    # Convert messages
    api_messages = convert_messages_to_api_format(
        messages,
        shrink_trailing_newlines=shrink,
    )

    # Build system prompt blocks with cache control
    caching_enabled = prompt_caching and not skip_cache_write and model_supports_prompt_caching(resolved_model)
    system_blocks = build_system_prompt_with_cache(
        system_prompt,
        prompt_caching_enabled=caching_enabled,
    )

    # Add cache breakpoints to messages and tools
    if caching_enabled:
        tools_list = _tools_to_dicts(tools)
        api_messages, tools_list, _ = add_cache_breakpoints(
            api_messages, tools_list, system_blocks
        )
    else:
        tools_list = _tools_to_dicts(tools)

    # Resolve thinking config
    if thinking_config is None and model_supports_thinking(resolved_model):
        thinking_config = build_thinking_config(resolved_model, enable_thinking=True)

    # Compute max_tokens
    max_tokens = max_tokens_override or get_max_output_tokens_for_model(resolved_model)

    # Beta headers
    betas = get_beta_headers(
        tools=tools_list,
        model=resolved_model,
        enable_prompt_caching=caching_enabled,
        enable_structured_outputs=enable_structured_outputs or bool(json_schema),
        enable_effort=enable_effort,
        enable_fast_mode=enable_fast_mode,
        enable_afk_mode=enable_afk_mode,
        enable_context_management=enable_context_management,
        enable_task_budgets=enable_task_budgets,
        enable_tool_search=enable_tool_search,
    )

    return APIRequestPayload(
        messages=api_messages,
        system=system_blocks,
        model=resolved_model,
        max_tokens=max_tokens,
        tools=tools_list,
        thinking=thinking_config,
        betas=betas,
        stream=stream,
        tool_choice=tool_choice,
    )


def _tools_to_dicts(tools: Sequence[Any]) -> list[dict[str, Any]]:
    """Convert tool objects to API-compatible dicts."""
    result = []
    for tool in tools:
        if isinstance(tool, dict):
            result.append(tool)
        elif hasattr(tool, "name"):
            schema_fn = getattr(tool, "input_schema", None)
            schema = schema_fn() if callable(schema_fn) else {}
            result.append({
                "name": tool.name,
                "description": getattr(tool, "description", tool.name),
                "input_schema": schema,
            })
        else:
            # Unknown tool type — try to make a best-effort dict
            result.append({"name": str(tool), "input_schema": {}})
    return result


# ---------------------------------------------------------------------------
# Stream event processing
# ---------------------------------------------------------------------------

# Stop reasons that indicate a final / terminal event
_TERMINAL_STOP_REASONS: frozenset[str] = frozenset({
    "end_turn",
    "max_tokens",
    "stop_sequence",
    "tool_use",
    "refusal",
})

# Stop reasons that indicate an error condition
_ERROR_STOP_REASONS: frozenset[str] = frozenset({
    "max_tokens",
    "refusal",
    "model_context_window_exceeded",
})


def is_streaming_final_event(event_type: str, stop_reason: Optional[str] = None) -> bool:
    """Check if a streaming event signals the end of the stream.

    Terminal events:
    - message_stop: the full message is complete
    - message_delta with terminal stop_reason: last delta before stop
    """
    if event_type == "message_stop":
        return True
    if event_type == "message_delta" and stop_reason is not None:
        return True
    return False


def classify_stream_stop_reason(stop_reason: Optional[str]) -> str:
    """Classify a stream stop reason into a category.

    Returns one of:
    - 'normal'       — end_turn, stop_sequence, tool_use
    - 'max_tokens'   — response hit token limit
    - 'refusal'      — content moderation refusal
    - 'context_exceeded' — context window exceeded
    - 'unknown'      — unrecognized stop reason
    """
    if stop_reason is None:
        return "unknown"
    if stop_reason == "end_turn":
        return "normal"
    if stop_reason == "stop_sequence":
        return "normal"
    if stop_reason == "tool_use":
        return "normal"
    if stop_reason == "max_tokens":
        return "max_tokens"
    if stop_reason == "refusal":
        return "refusal"
    if stop_reason == "model_context_window_exceeded":
        return "context_exceeded"
    return "unknown"


def is_error_stop_reason(stop_reason: Optional[str]) -> bool:
    """Check if a stop reason indicates an error condition."""
    if stop_reason is None:
        return False
    return stop_reason in _ERROR_STOP_REASONS


def is_terminal_stop_reason(stop_reason: Optional[str]) -> bool:
    """Check if a stop reason is terminal (the model has finished)."""
    if stop_reason is None:
        return False
    return stop_reason in _TERMINAL_STOP_REASONS


# ---------------------------------------------------------------------------
# Streaming error handling
# ---------------------------------------------------------------------------

@dataclass
class StreamErrorInfo:
    """Structured information about a streaming error."""

    is_retryable: bool
    status_code: int
    error_type: str
    message: str
    retry_after_seconds: Optional[float] = None
    raw_error: Optional[Exception] = None


def classify_streaming_error(error: Exception) -> StreamErrorInfo:
    """Classify a streaming error to determine retry strategy.

    Examines the error type, status code, and message to produce a
    StreamErrorInfo with retryability and classification.
    """
    # Extract status code from various error types
    status = _extract_status_from_error(error)
    msg = str(error) if error else ""
    error_type = type(error).__name__

    is_retryable = status in (429, 502, 503, 529) or any(
        kw in msg.lower()
        for kw in ("overloaded", "rate limit", "timeout", "connection", "reset")
    )

    # Extract retry-after if available
    retry_after = None
    headers = getattr(error, "headers", None)
    if isinstance(headers, dict):
        raw_ra = headers.get("retry-after")
        if raw_ra is not None:
            try:
                retry_after = float(raw_ra)
            except (ValueError, TypeError):
                pass

    classification = "unknown"
    if status == 429:
        classification = "rate_limit"
    elif status == 529:
        classification = "overloaded"
    elif status == 502 or status == 503:
        classification = "server_error"
    elif status == 401 or status == 403:
        classification = "auth_error"
    elif status == 400:
        if "invalid model" in msg.lower():
            classification = "invalid_model"
        elif "prompt is too long" in msg.lower():
            classification = "prompt_too_long"
        else:
            classification = "bad_request"
    elif "timeout" in msg.lower():
        classification = "timeout"
    elif "connection" in msg.lower() or "reset" in msg.lower():
        classification = "connection"

    return StreamErrorInfo(
        is_retryable=is_retryable,
        status_code=status,
        error_type=classification,
        message=msg,
        retry_after_seconds=retry_after,
        raw_error=error,
    )


def _extract_status_from_error(error: Exception) -> int:
    """Extract HTTP status code from various error types."""
    if hasattr(error, "status_code"):
        return getattr(error, "status_code", 0)
    if hasattr(error, "status"):
        return getattr(error, "status", 0)
    if hasattr(error, "response") and hasattr(error.response, "status_code"):
        return error.response.status_code
    msg = str(error).lower()
    if "529" in msg or "overloaded" in msg:
        return 529
    if "429" in msg or "rate limit" in msg:
        return 429
    if "503" in msg:
        return 503
    if "502" in msg:
        return 502
    if "401" in msg or "unauthorized" in msg:
        return 401
    if "403" in msg or "forbidden" in msg:
        return 403
    if "400" in msg or "bad request" in msg:
        return 400
    return 0


def is_transient_stream_error(error: Exception) -> bool:
    """Check if a streaming error is transient and can be retried."""
    info = classify_streaming_error(error)
    return info.is_retryable


def handle_streaming_error(
    error: Exception,
    model: str = "",
    fallback_model: Optional[str] = None,
) -> dict[str, Any]:
    """Create an appropriate error response for a streaming error.

    Generates a user-friendly error message based on the error type.
    """
    info = classify_streaming_error(error)

    if info.error_type == "rate_limit":
        return {
            "error": "rate_limit",
            "message": (
                f"Rate limit exceeded. Please wait before sending more requests. "
                f"{'Try again in a moment.' if not info.retry_after_seconds else f'Retry after {info.retry_after_seconds:.0f} seconds.'}"
            ),
        }
    if info.error_type == "overloaded":
        return {
            "error": "overloaded",
            "message": (
                "Claude is temporarily overloaded. Please try again in a few moments. "
                f"{'Falling back to ' + fallback_model + '.' if fallback_model else ''}"
            ),
        }
    if info.error_type == "server_error":
        return {
            "error": "server_error",
            "message": "Anthropic server error. Please try again.",
        }
    if info.error_type == "auth_error":
        return {
            "error": "auth_error",
            "message": "Authentication failed. Please run /login or check your API key.",
        }
    if info.error_type == "invalid_model":
        return {
            "error": "invalid_model",
            "message": f"The model '{model}' is not available. Try running /model to switch.",
        }
    if info.error_type == "prompt_too_long":
        return {
            "error": "prompt_too_long",
            "message": "Prompt is too long. Try reducing the conversation length with /compact.",
        }
    if info.error_type == "timeout":
        return {
            "error": "timeout",
            "message": "Request timed out. Check your internet connection and try again.",
        }
    if info.error_type == "connection":
        return {
            "error": "connection",
            "message": "Connection error. Check your internet connection and proxy settings.",
        }
    return {
        "error": "unknown",
        "message": f"API Error: {info.message}",
    }


# ---------------------------------------------------------------------------
# Main streaming entry point
# ---------------------------------------------------------------------------

async def query_model_with_streaming(payload: dict[str, Any]) -> Any:
    """Streaming model call — async generator yielding AssistantMessage items.

    Accepts payload dict from query/core.py's _stream_model_turn:
        {
            "messages": [...],
            "system_prompt": [...],
            "thinking_config": {...},
            "tools": [...],
            "signal": <AbortSignal-like>,
            "options": {
                "model": "...",
                "fallback_model": "...",
                "query_source": "...",
                "max_output_tokens_override": ...,
                "skip_cache_write": False,
                ...
            },
        }

    Uses build_api_request_payload() to assemble the request and delegates
    to call_model_api from client.py for the actual SDK call.
    """
    from hare.services.api.client import call_model_api

    messages = payload.get("messages", [])
    system_prompt = payload.get("system_prompt", [])
    options = payload.get("options", {})
    model = options.get("model", "")
    tools = payload.get("tools", [])
    thinking_config = payload.get("thinking_config")
    json_schema = payload.get("json_schema")

    # Use the centralized request builder
    api_payload = build_api_request_payload(
        messages=messages,
        system_prompt=system_prompt,
        model=model,
        tools=tools,
        thinking_config=thinking_config,
        max_tokens_override=options.get("max_output_tokens_override"),
        stream=True,
        prompt_caching=not options.get("skip_cache_write", False),
        enable_structured_outputs=bool(json_schema),
        enable_effort=bool(options.get("enable_effort", False)),
        enable_fast_mode=bool(options.get("enable_fast_mode", False)),
        enable_afk_mode=bool(options.get("enable_afk_mode", False)),
        enable_context_management=bool(options.get("enable_context_management", False)),
        enable_task_budgets=bool(options.get("enable_task_budgets", False)),
        enable_tool_search=bool(options.get("enable_tool_search", False)),
        json_schema=json_schema,
        skip_cache_write=options.get("skip_cache_write", False),
    )

    result = call_model_api(
        messages=api_payload.messages,
        system_prompt=[b.get("text", "") for b in api_payload.system if isinstance(b, dict)],
        model=api_payload.model,
        tools=tools,
        thinking_config=api_payload.thinking,
        max_tokens=api_payload.max_tokens,
        stream=True,
        fallback_model=options.get("fallback_model"),
        on_streaming_fallback=options.get("on_streaming_fallback"),
    )

    return result


async def stream_message_beta(params: dict[str, Any]) -> Any:
    """Streaming entrypoint — delegates to query_model_with_streaming."""
    return await query_model_with_streaming(params)


# ---------------------------------------------------------------------------
# Non-streaming fallback
# ---------------------------------------------------------------------------

async def query_model_without_streaming(payload: dict[str, Any]) -> Any:
    """Non-streaming model call — returns a single AssistantMessage.

    Used as a fallback when streaming is unavailable or for simple
    one-shot queries where streaming overhead is unnecessary.
    """
    from hare.services.api.client import call_model_api

    messages = payload.get("messages", [])
    system_prompt = payload.get("system_prompt", [])
    options = payload.get("options", {})
    model = options.get("model", "")
    tools = payload.get("tools", [])
    thinking_config = payload.get("thinking_config")
    json_schema = payload.get("json_schema")

    api_payload = build_api_request_payload(
        messages=messages,
        system_prompt=system_prompt,
        model=model,
        tools=tools,
        thinking_config=thinking_config,
        max_tokens_override=options.get("max_output_tokens_override"),
        stream=False,
        prompt_caching=not options.get("skip_cache_write", False),
        enable_structured_outputs=bool(json_schema),
        json_schema=json_schema,
        skip_cache_write=options.get("skip_cache_write", False),
    )

    return call_model_api(
        messages=api_payload.messages,
        system_prompt=[b.get("text", "") for b in api_payload.system if isinstance(b, dict)],
        model=api_payload.model,
        tools=tools,
        thinking_config=api_payload.thinking,
        max_tokens=api_payload.max_tokens,
        stream=False,
    )


# ---------------------------------------------------------------------------
# Multi-turn conversation helpers
# ---------------------------------------------------------------------------

def build_conversation_messages(
    turns: list[dict[str, Any]],
    *,
    system_prompt: Optional[list[str]] = None,
    model: str = "",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build message list and system blocks from conversation turns.

    Each turn is a dict with 'role' ('user' or 'assistant') and 'content'.
    System turns are extracted into the system prompt.
    """
    messages: list[dict[str, Any]] = []
    system_blocks: list[dict[str, Any]] = []

    for turn in turns:
        role = turn.get("role", "")
        content = turn.get("content", "")
        if role == "system":
            system_blocks.append({"type": "text", "text": str(content)})
        elif role in ("user", "assistant"):
            messages.append({"role": role, "content": content})

    # Append explicit system_prompt
    if system_prompt:
        for text in system_prompt:
            system_blocks.append({"type": "text", "text": text})

    return messages, system_blocks


def estimate_token_count(messages: list[dict[str, Any]], system_prompt: Optional[list[str]] = None) -> int:
    """Estimate the token count of a conversation.

    Uses a rough heuristic: ~4 chars per token for English text.
    For accurate counts, use the Anthropic token-counting API.
    """
    total_chars = 0

    if system_prompt:
        for text in system_prompt:
            total_chars += len(text)

    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total_chars += len(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        total_chars += len(block.get("text", ""))
                    elif block.get("type") == "tool_use":
                        total_chars += len(str(block.get("input", {})))
                    elif block.get("type") == "tool_result":
                        total_chars += len(str(block.get("content", "")))

    # ~4 characters per token (rough heuristic for English)
    return max(1, total_chars // 4)


# ---------------------------------------------------------------------------
# Model string normalization
# ---------------------------------------------------------------------------

def normalize_model_string_for_api(model: str) -> str:
    """Remove context window suffixes like [1m] for API calls."""
    return re.sub(r"\[\d+m\]", "", model, flags=re.IGNORECASE)


def first_party_name_to_canonical(name: str) -> str:
    """Map a first-party model name to its canonical short form.

    E.g., 'claude-opus-4-6-20260301' -> 'claude-opus-4-6'
    """
    lower = name.lower()
    checks = [
        ("claude-opus-4-6", "claude-opus-4-6"),
        ("claude-opus-4-5", "claude-opus-4-5"),
        ("claude-opus-4-1", "claude-opus-4-1"),
        ("claude-opus-4", "claude-opus-4"),
        ("claude-sonnet-4-6", "claude-sonnet-4-6"),
        ("claude-sonnet-4-5", "claude-sonnet-4-5"),
        ("claude-sonnet-4", "claude-sonnet-4"),
        ("claude-haiku-4-5", "claude-haiku-4-5"),
        ("hare-3-7-sonnet", "hare-3-7-sonnet"),
        ("hare-3-5-sonnet", "hare-3-5-sonnet"),
        ("hare-3-5-haiku", "hare-3-5-haiku"),
        ("hare-3-opus", "hare-3-opus"),
        ("hare-3-sonnet", "hare-3-sonnet"),
        ("hare-3-haiku", "hare-3-haiku"),
    ]
    for check_str, canonical in checks:
        if check_str in lower:
            return canonical
    match = re.match(r"(hare-(?:\d+-\d+-)?\w+)", lower)
    if match:
        return match.group(1)
    return name


# ---------------------------------------------------------------------------
# Convenience: single-turn call
# ---------------------------------------------------------------------------

async def single_turn_query(
    *,
    prompt: str,
    system_prompt: str = "",
    model: str = "",
    max_tokens: Optional[int] = None,
    tools: Sequence[Any] = (),
    temperature: float = 1.0,
    stream: bool = False,
) -> Any:
    """Convenience function for a single-turn (one-shot) query.

    Builds a request with a single user message and returns the response.
    For multi-turn conversations, use query_model_with_streaming() directly.
    """
    from hare.services.api.client import call_model_api

    resolved_model = model or get_default_model()
    api_messages = [{"role": "user", "content": prompt}]
    sys_blocks: list[str] = [system_prompt] if system_prompt else []
    max_tok = max_tokens or get_max_output_tokens_for_model(resolved_model)

    return call_model_api(
        messages=api_messages,
        system_prompt=sys_blocks,
        model=resolved_model,
        tools=tools,
        max_tokens=max_tok,
        stream=stream,
    )


# ---------------------------------------------------------------------------
# Pre-compute / validate model compatibility
# ---------------------------------------------------------------------------

def validate_model_compatibility(
    model: str,
    features: Optional[dict[str, bool]] = None,
) -> dict[str, Any]:
    """Validate that a model supports the requested features.

    Returns a dict with 'compatible' (bool) and 'warnings' (list[str]).
    """
    warnings: list[str] = []
    if features is None:
        features = {}

    checks = [
        ("thinking", model_supports_thinking, features.get("thinking", False)),
        ("effort", model_supports_effort, features.get("effort", False)),
        ("structured_outputs", model_supports_structured_outputs, features.get("structured_outputs", False)),
        ("image", model_supports_image, features.get("image", False)),
        ("web_search", model_supports_web_search, features.get("web_search", False)),
        ("tool_search", model_supports_tool_search, features.get("tool_search", False)),
        ("prompt_caching", model_supports_prompt_caching, features.get("prompt_caching", False)),
        ("context_management", model_supports_context_management, features.get("context_management", False)),
        ("fast_mode", model_supports_fast_mode, features.get("fast_mode", False)),
        ("task_budgets", model_supports_task_budgets, features.get("task_budgets", False)),
        ("interleaved_thinking", model_supports_interleaved_thinking, features.get("interleaved_thinking", False)),
        ("1m_context", model_supports_1m_context, features.get("1m_context", False)),
    ]

    for feature_name, checker, requested in checks:
        if requested and not checker(model):
            warnings.append(
                f"Model '{model}' does not support '{feature_name}'. "
                f"This feature will be silently disabled."
            )

    return {
        "compatible": True,  # Always compatible — features just won't be enabled
        "warnings": warnings,
        "deprecated": is_model_deprecated(model),
        "context_window": get_model_context_window(model),
        "model_family": classify_model_family(model),
    }


# ---------------------------------------------------------------------------
# System prompt content hash (for cache break detection)
# ---------------------------------------------------------------------------

def hash_system_prompt(system_prompt: list[str]) -> str:
    """Compute a stable hash of the system prompt for cache break detection."""
    content = "\n".join(system_prompt)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def hash_tools(tools: list[dict[str, Any]]) -> str:
    """Compute a stable hash of tool definitions for cache break detection."""
    parts = []
    for t in tools:
        name = t.get("name", "")
        desc = t.get("description", "")
        parts.append(f"{name}:{desc}")
    content = "|".join(sorted(parts))
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
