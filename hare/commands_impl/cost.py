"""
/cost command - show estimated cost for the current session.

Port of: src/commands/cost/cost.tsx + index.ts

Estimates cost based on token usage and model pricing.
"""

from __future__ import annotations

from typing import Any

COMMAND_NAME = "cost"
DESCRIPTION = "Show estimated API cost"
ALIASES: list[str] = []

# Approximate pricing per 1M tokens (USD)
MODEL_PRICING = {
    "claude-opus-4-7": {"input": 15.00, "output": 75.00},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
    "claude-haiku-4-5": {"input": 0.80, "output": 4.00},
    "claude-opus-4": {"input": 15.00, "output": 75.00},
    "claude-sonnet-4": {"input": 3.00, "output": 15.00},
    "claude-haiku-3.5": {"input": 0.80, "output": 4.00},
    "default": {"input": 3.00, "output": 15.00},
}


async def call(args: str, **context: Any) -> dict[str, Any]:
    """Show estimated cost."""
    get_usage_stats = context.get("get_usage_stats")
    options = context.get("options", {})
    model = options.get("mainLoopModel", "default")

    pricing = MODEL_PRICING.get(model, MODEL_PRICING["default"])

    if get_usage_stats:
        stats = get_usage_stats()
        input_tokens = stats.get("input_tokens", 0)
        output_tokens = stats.get("output_tokens", 0)
    else:
        input_tokens = 0
        output_tokens = 0

    input_cost = (input_tokens / 1_000_000) * pricing["input"]
    output_cost = (output_tokens / 1_000_000) * pricing["output"]
    total_cost = input_cost + output_cost

    lines = [
        "## Cost Estimate",
        "",
        f"**Model:** {model}",
        f"**Input tokens:** {input_tokens:,} (${input_cost:.4f})",
        f"**Output tokens:** {output_tokens:,} (${output_cost:.4f})",
        f"**Total cost:** ${total_cost:.4f}",
        "",
        "*Cost is an estimate. Check your Anthropic Console for exact billing.*",
    ]

    return {"type": "text", "value": "\n".join(lines)}


def get_command_definition() -> dict[str, Any]:
    return {
        "type": "local",
        "name": COMMAND_NAME,
        "description": DESCRIPTION,
        "aliases": ALIASES,
        "call": call,
    }
