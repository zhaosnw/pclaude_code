"""
Cost hook – display cost summary on exit.

Port of: src/costHook.ts (React hook → Python atexit)
"""

from __future__ import annotations

import atexit
from hare.cost_tracker import format_total_cost, save_current_session_costs


_registered = False


def register_cost_summary_hook() -> None:
    """Register an atexit hook to print cost summary."""
    global _registered
    if _registered:
        return
    _registered = True

    def _on_exit() -> None:
        try:
            summary = format_total_cost()
            if summary:
                print(f"\n{summary}")
            save_current_session_costs()
        except Exception:
            pass

    atexit.register(_on_exit)
