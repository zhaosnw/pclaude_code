"""Auto mode circuit-breaker and CLI flag state. Port of autoModeState.ts."""

from __future__ import annotations

_auto_mode_active = False
_auto_mode_flag_cli = False
_auto_mode_circuit_broken = False


def set_auto_mode_active(active: bool) -> None:
    global _auto_mode_active
    _auto_mode_active = active


def is_auto_mode_active() -> bool:
    return _auto_mode_active


def set_auto_mode_flag_cli(passed: bool) -> None:
    global _auto_mode_flag_cli
    _auto_mode_flag_cli = passed


def get_auto_mode_flag_cli() -> bool:
    return _auto_mode_flag_cli


def set_auto_mode_circuit_broken(broken: bool) -> None:
    global _auto_mode_circuit_broken
    _auto_mode_circuit_broken = broken


def is_auto_mode_circuit_broken() -> bool:
    return _auto_mode_circuit_broken


def reset_for_testing() -> None:
    global _auto_mode_active, _auto_mode_flag_cli, _auto_mode_circuit_broken
    _auto_mode_active = False
    _auto_mode_flag_cli = False
    _auto_mode_circuit_broken = False
