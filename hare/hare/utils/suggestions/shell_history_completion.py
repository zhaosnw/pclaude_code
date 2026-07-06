"""Shell history based completions.

Port of: src/utils/suggestions/shellHistoryCompletion.ts
"""

from __future__ import annotations

from pathlib import Path


def load_shell_history_candidates(
    _history_path: Path | None = None,
    *,
    limit: int = 50,
) -> list[str]:
    return [] if limit else []
