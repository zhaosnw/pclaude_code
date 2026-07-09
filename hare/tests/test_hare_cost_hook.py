"""Tests for hare.cost_hook — atexit cost summary hook."""

from __future__ import annotations

import atexit
import io
import sys
from unittest.mock import patch

import pytest
from hare.cost_tracker import reset_cost_tracker


@pytest.fixture(autouse=True)
def _reset_hook() -> None:
    import hare.cost_hook as ch

    ch._registered = False
    atexit._clear()


def test_register_idempotent() -> None:
    from hare.cost_hook import register_cost_summary_hook as register_hook

    reset_cost_tracker()
    register_hook()
    assert atexit._ncallbacks() == 1
    register_hook()
    assert atexit._ncallbacks() == 1


def test_register_no_cost_does_not_crash() -> None:
    from hare.cost_hook import register_cost_summary_hook as register_hook

    reset_cost_tracker()
    register_hook()
    atexit._run_exitfuncs()


def test_exit_with_cost_output() -> None:
    from hare.cost_hook import register_cost_summary_hook as register_hook

    reset_cost_tracker()

    buf = io.StringIO()
    with patch.object(sys, "stdout", buf):
        with patch(
            "hare.cost_hook.format_total_cost", return_value="Total cost: $0.50"
        ):
            register_hook()
            atexit._run_exitfuncs()
    assert "Total cost: $0.50" in buf.getvalue()


def test_exit_handles_format_error_gracefully() -> None:
    from hare.cost_hook import register_cost_summary_hook as register_hook

    reset_cost_tracker()

    with patch("hare.cost_hook.format_total_cost", side_effect=RuntimeError("boom")):
        register_hook()
        atexit._run_exitfuncs()
