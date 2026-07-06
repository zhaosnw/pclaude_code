"""Port of: src/utils/bash/registry.ts"""

from __future__ import annotations
from typing import Any

_command_registry: dict[str, dict[str, Any]] = {}


def register_command_spec(name: str, spec: dict[str, Any]) -> None:
    _command_registry[name] = spec


def get_command_spec(name: str) -> dict[str, Any] | None:
    return _command_registry.get(name)


def get_all_command_specs() -> dict[str, dict[str, Any]]:
    return dict(_command_registry)
