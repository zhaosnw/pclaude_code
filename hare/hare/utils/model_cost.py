"""Per-model USD pricing — port of `modelCost.ts`."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hare.utils.fast_mode import is_fast_mode_enabled


@dataclass
class ModelCosts:
    input_tokens: float
    output_tokens: float
    prompt_cache_write_tokens: float
    prompt_cache_read_tokens: float
    web_search_requests: float


COST_TIER_3_15 = ModelCosts(3, 15, 3.75, 0.3, 0.01)
COST_TIER_15_75 = ModelCosts(15, 75, 18.75, 1.5, 0.01)
COST_TIER_5_25 = ModelCosts(5, 25, 6.25, 0.5, 0.01)
COST_TIER_30_150 = ModelCosts(30, 150, 37.5, 3, 0.01)
COST_HAIKU_35 = ModelCosts(0.8, 4, 1, 0.08, 0.01)
COST_HAIKU_45 = ModelCosts(1, 5, 1.25, 0.1, 0.01)

DEFAULT_UNKNOWN_MODEL_COST = COST_TIER_5_25

# Canonical short name -> costs (align with `model/model.ts` naming)
MODEL_COSTS: dict[str, ModelCosts] = {
    "hare-3-5-haiku-20241022": COST_HAIKU_35,
    "claude-haiku-4-5-20250514": COST_HAIKU_45,
    "hare-3-5-sonnet-20241022": COST_TIER_3_15,
    "hare-3-7-sonnet-20250219": COST_TIER_3_15,
    "claude-sonnet-4-20250514": COST_TIER_3_15,
    "claude-sonnet-4-5-20241022": COST_TIER_3_15,
    "claude-sonnet-4-6-20260301": COST_TIER_3_15,
    "claude-opus-4-20250514": COST_TIER_15_75,
    "claude-opus-4-1-20250805": COST_TIER_15_75,
    "claude-opus-4-5-20250514": COST_TIER_5_25,
    "claude-opus-4-6-20260301": COST_TIER_5_25,
}


def get_canonical_name(model: str) -> str:
    return model.strip().lower()


def get_default_main_loop_model_setting() -> str:
    from hare.utils.model import get_default_sonnet_model  # type: ignore[circular]

    return get_default_sonnet_model()


def get_opus_46_cost_tier(fast_mode: bool) -> ModelCosts:
    if is_fast_mode_enabled() and fast_mode:
        return COST_TIER_30_150
    return COST_TIER_5_25


def get_model_costs(model: str, usage: Any) -> ModelCosts:
    short = get_canonical_name(model)
    if "opus-4-6" in short or short.endswith("opus-4-6-20260301"):
        is_fast = getattr(usage, "speed", None) == "fast" if usage else False
        return get_opus_46_cost_tier(is_fast)
    c = MODEL_COSTS.get(short)
    if c:
        return c
    try:
        from hare.bootstrap.state import set_has_unknown_model_cost  # type: ignore[import-not-found]
    except ImportError:

        def set_has_unknown_model_cost() -> None:
            pass

    set_has_unknown_model_cost()
    return MODEL_COSTS.get(
        get_canonical_name(get_default_main_loop_model_setting()),
        DEFAULT_UNKNOWN_MODEL_COST,
    )


def tokens_to_usd_cost(costs: ModelCosts, usage: Any) -> float:
    inp = (
        getattr(usage, "input_tokens", None) or usage.get("input_tokens", 0)
        if isinstance(usage, dict)
        else getattr(usage, "input_tokens", 0)
    )
    out = (
        getattr(usage, "output_tokens", None) or usage.get("output_tokens", 0)
        if isinstance(usage, dict)
        else getattr(usage, "output_tokens", 0)
    )
    cr = getattr(usage, "cache_read_input_tokens", None) or (
        usage.get("cache_read_input_tokens", 0) if isinstance(usage, dict) else 0
    )
    cc = getattr(usage, "cache_creation_input_tokens", None) or (
        usage.get("cache_creation_input_tokens", 0) if isinstance(usage, dict) else 0
    )
    ws = 0
    st = getattr(usage, "server_tool_use", None)
    if isinstance(st, dict):
        ws = st.get("web_search_requests", 0) or 0
    return (
        (inp / 1_000_000) * costs.input_tokens
        + (out / 1_000_000) * costs.output_tokens
        + (cr / 1_000_000) * costs.prompt_cache_read_tokens
        + (cc / 1_000_000) * costs.prompt_cache_write_tokens
        + ws * costs.web_search_requests
    )


def calculate_usd_cost(resolved_model: str, usage: Any) -> float:
    return tokens_to_usd_cost(get_model_costs(resolved_model, usage), usage)


def calculate_cost_from_tokens(
    model: str,
    tokens: dict[str, int],
) -> float:
    usage = type(
        "U",
        (),
        {
            "input_tokens": tokens["inputTokens"],
            "output_tokens": tokens["outputTokens"],
            "cache_read_input_tokens": tokens["cacheReadInputTokens"],
            "cache_creation_input_tokens": tokens["cacheCreationInputTokens"],
        },
    )()
    return calculate_usd_cost(model, usage)


def format_model_pricing(costs: ModelCosts) -> str:
    def fp(p: float) -> str:
        return f"${int(p)}" if float(p).is_integer() else f"${p:.2f}"

    return f"{fp(costs.input_tokens)}/{fp(costs.output_tokens)} per Mtok"


def get_model_pricing_string(model: str) -> str | None:
    short = get_canonical_name(model)
    c = MODEL_COSTS.get(short)
    if not c:
        return None
    return format_model_pricing(c)
