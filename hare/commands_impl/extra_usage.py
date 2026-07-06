"""
/extra-usage command - extended usage statistics with detailed breakdown.

Port of: src/commands/extra-usage/ (4 files: index.ts, core.ts, interactive.ts, noninteractive.ts)

Shows detailed token usage broken down by:
  - Core vs non-interactive vs interactive usage
  - Cache creation vs cache read
  - Per-model breakdown
"""

from __future__ import annotations

from typing import Any

COMMAND_NAME = "extra-usage"
DESCRIPTION = "Show extended usage statistics"
ALIASES: list[str] = []


async def call(args: str, **context: Any) -> dict[str, Any]:
    """Show extended usage statistics."""
    get_extra_usage_stats = context.get("get_extra_usage_stats")

    if get_extra_usage_stats:
        stats = get_extra_usage_stats()
    else:
        return {"type": "text", "value": "Extended usage statistics not available."}

    lines = ["## Extended Usage Statistics", ""]

    # Core usage
    core = stats.get("core", {})
    if core:
        lines.append("### Core Usage")
        lines.append(f"- Input tokens: {core.get('input_tokens', 0):,}")
        lines.append(f"- Output tokens: {core.get('output_tokens', 0):,}")
        lines.append(
            f"- Cache creation tokens: {core.get('cache_creation_input_tokens', 0):,}"
        )
        lines.append(f"- Cache read tokens: {core.get('cache_read_input_tokens', 0):,}")
        lines.append("")

    # Non-interactive usage
    noninteractive = stats.get("noninteractive", {})
    if noninteractive:
        lines.append("### Non-Interactive Usage")
        lines.append(f"- Input tokens: {noninteractive.get('input_tokens', 0):,}")
        lines.append(f"- Output tokens: {noninteractive.get('output_tokens', 0):,}")
        lines.append("")

    # Interactive usage
    interactive = stats.get("interactive", {})
    if interactive:
        lines.append("### Interactive Usage")
        lines.append(f"- Input tokens: {interactive.get('input_tokens', 0):,}")
        lines.append(f"- Output tokens: {interactive.get('output_tokens', 0):,}")
        lines.append("")

    # Totals
    total_input = (
        core.get("input_tokens", 0)
        + noninteractive.get("input_tokens", 0)
        + interactive.get("input_tokens", 0)
    )
    total_output = (
        core.get("output_tokens", 0)
        + noninteractive.get("output_tokens", 0)
        + interactive.get("output_tokens", 0)
    )
    lines.append("### Totals")
    lines.append(f"- **Total input:** {total_input:,}")
    lines.append(f"- **Total output:** {total_output:,}")
    lines.append(f"- **Total:** {total_input + total_output:,}")

    return {"type": "text", "value": "\n".join(lines)}


def get_command_definition() -> dict[str, Any]:
    return {
        "type": "local",
        "name": COMMAND_NAME,
        "description": DESCRIPTION,
        "aliases": ALIASES,
        "call": call,
    }
