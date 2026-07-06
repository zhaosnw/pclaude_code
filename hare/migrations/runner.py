"""
Migration runner — invokes individual migrations.

Port of: src/migrations/ (orchestration); per-migration modules are separate files.
"""

from __future__ import annotations

import json
import os
from typing import Any

MIGRATIONS_STATE_FILE = os.path.join(
    os.path.expanduser("~"), ".hare", "migrations.json"
)


def _load_state() -> dict[str, Any]:
    if not os.path.isfile(MIGRATIONS_STATE_FILE):
        return {"completed": []}
    try:
        with open(MIGRATIONS_STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except OSError:
        return {"completed": []}


def _save_state(state: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(MIGRATIONS_STATE_FILE), exist_ok=True)
    with open(MIGRATIONS_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def _run_registered_migrations() -> list[str]:
    """Import and run idempotent migrations once each (tracked by id)."""
    from hare.migrations.migrate_repl_bridge_enabled_to_remote_control_at_startup import (
        migrate_repl_bridge_enabled_to_remote_control_at_startup,
    )

    ids: list[tuple[str, object]] = [
        (
            "repl_bridge_to_remote_control",
            migrate_repl_bridge_enabled_to_remote_control_at_startup,
        ),
    ]
    newly_run: list[str] = []
    for mid, fn in ids:
        fn()  # type: ignore[misc]
        newly_run.append(mid)
    return newly_run


async def run_migrations() -> list[str]:
    state = _load_state()
    completed = list(state.get("completed", []))
    newly_run = _run_registered_migrations()
    for m in newly_run:
        if m not in completed:
            completed.append(m)
    state["completed"] = completed
    _save_state(state)
    return newly_run
