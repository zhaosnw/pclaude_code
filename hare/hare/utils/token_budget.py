"""Token budget parsing and continuation-message formatting.

Port of: src/utils/tokenBudget.ts (line-by-line).
"""

from __future__ import annotations

import re
from typing import TypedDict

# -- src/utils/tokenBudget.ts L1-9
#
# JS comments preserved:
# Shorthand (+500k) anchored to start/end to avoid false positives in natural
# language. Verbose (use/spend 2M tokens) matches anywhere.
_SHORTHAND_START_RE = re.compile(r"^\s*\+(\d+(?:\.\d+)?)\s*(k|m|b)\b", re.IGNORECASE)

# Lookbehind (?<=\s) is avoided — it defeats YARR JIT in JSC, and the
# interpreter scans O(n) even with the $ anchor. Capture the whitespace
# instead; callers offset match.index by 1 where position matters.
_SHORTHAND_END_RE = re.compile(
    r"\s\+(\d+(?:\.\d+)?)\s*(k|m|b)\s*[.!?]?\s*$", re.IGNORECASE
)
_VERBOSE_RE = re.compile(
    r"\b(?:use|spend)\s+(\d+(?:\.\d+)?)\s*(k|m|b)\s*tokens?\b", re.IGNORECASE
)
_VERBOSE_RE_G = re.compile(_VERBOSE_RE.pattern, re.IGNORECASE)


# -- src/utils/tokenBudget.ts L11-15
_MULTIPLIERS: dict[str, int] = {
    "k": 1_000,
    "m": 1_000_000,
    "b": 1_000_000_000,
}


# -- src/utils/tokenBudget.ts L17-19
def _parse_budget_match(value: str, suffix: str) -> int:
    return int(float(value) * _MULTIPLIERS[suffix.lower()])


# -- src/utils/tokenBudget.ts L21-29
def parse_token_budget(text: str) -> int | None:
    start_match = _SHORTHAND_START_RE.search(text)
    if start_match:
        return _parse_budget_match(start_match.group(1), start_match.group(2))
    end_match = _SHORTHAND_END_RE.search(text)
    if end_match:
        return _parse_budget_match(end_match.group(1), end_match.group(2))
    verbose_match = _VERBOSE_RE.search(text)
    if verbose_match:
        return _parse_budget_match(verbose_match.group(1), verbose_match.group(2))
    return None


class _Position(TypedDict):
    start: int
    end: int


# -- src/utils/tokenBudget.ts L31-64
def find_token_budget_positions(text: str) -> list[_Position]:
    positions: list[_Position] = []
    start_match = _SHORTHAND_START_RE.search(text)
    if start_match:
        # Trim leading whitespace from the captured shorthand.
        offset = (
            start_match.start()
            + len(start_match.group(0))
            - len(start_match.group(0).lstrip())
        )
        positions.append(
            {"start": offset, "end": start_match.start() + len(start_match.group(0))}
        )
    end_match = _SHORTHAND_END_RE.search(text)
    if end_match:
        # Avoid double-counting when input is just "+500k".
        end_start = end_match.start() + 1  # +1: regex includes leading \s
        already_covered = any(p["start"] <= end_start < p["end"] for p in positions)
        if not already_covered:
            positions.append(
                {"start": end_start, "end": end_match.start() + len(end_match.group(0))}
            )
    for match in _VERBOSE_RE_G.finditer(text):
        positions.append({"start": match.start(), "end": match.end()})
    return positions


# -- src/utils/tokenBudget.ts L66-73
def get_budget_continuation_message(pct: int, turn_tokens: int, budget: int) -> str:
    # TS uses Intl.NumberFormat('en-US'); Python's `format(n, ',')` matches.
    fmt = lambda n: format(n, ",")  # noqa: E731
    return (
        f"Stopped at {pct}% of token target "
        f"({fmt(turn_tokens)} / {fmt(budget)}). Keep working \u2014 do not summarize."
    )
