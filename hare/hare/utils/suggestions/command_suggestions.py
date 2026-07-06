"""
Command autocomplete suggestions.

Port of: src/utils/suggestions/commandSuggestions.ts
"""

from __future__ import annotations

from typing import Any


def get_command_suggestions(
    input_text: str,
    commands: list[dict[str, Any]],
    max_results: int = 10,
) -> list[dict[str, Any]]:
    """Get command suggestions for the given input text."""
    if not input_text.startswith("/"):
        return []
    query = input_text[1:].lower()
    if not query:
        return commands[:max_results]

    scored: list[tuple[float, dict[str, Any]]] = []
    for cmd in commands:
        name = cmd.get("name", "").lower()
        if name.startswith(query):
            scored.append((0, cmd))
        elif query in name:
            scored.append((1, cmd))
        desc = cmd.get("description", "").lower()
        if query in desc:
            scored.append((2, cmd))

    scored.sort(key=lambda x: x[0])
    seen: set[str] = set()
    results: list[dict[str, Any]] = []
    for _, cmd in scored:
        name = cmd.get("name", "")
        if name not in seen:
            seen.add(name)
            results.append(cmd)
            if len(results) >= max_results:
                break
    return results


def find_slash_command_positions(text: str) -> list[dict[str, int]]:
    """Find positions of slash commands in text."""
    positions: list[dict[str, int]] = []
    i = 0
    while i < len(text):
        if text[i] == "/" and (i == 0 or text[i - 1] in (" ", "\n")):
            start = i
            i += 1
            while i < len(text) and text[i].isalnum():
                i += 1
            positions.append({"start": start, "end": i})
        else:
            i += 1
    return positions
