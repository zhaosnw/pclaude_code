"""
Validation helpers for numeric environment variables.

Port of: src/utils/envValidation.ts
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from hare.utils.debug import log_for_debugging


@dataclass
class EnvVarValidationResult:
    effective: int
    status: Literal["valid", "capped", "invalid"]
    message: str | None = None


def validate_bounded_int_env_var(
    name: str,
    value: str | None,
    default_value: int,
    upper_limit: int,
) -> EnvVarValidationResult:
    if not value:
        return EnvVarValidationResult(effective=default_value, status="valid")
    try:
        parsed = int(value, 10)
    except ValueError:
        msg = f'Invalid value "{value}" (using default: {default_value})'
        log_for_debugging(f"{name} {msg}")
        return EnvVarValidationResult(
            effective=default_value, status="invalid", message=msg
        )
    if parsed <= 0:
        msg = f'Invalid value "{value}" (using default: {default_value})'
        log_for_debugging(f"{name} {msg}")
        return EnvVarValidationResult(
            effective=default_value, status="invalid", message=msg
        )
    if parsed > upper_limit:
        msg = f"Capped from {parsed} to {upper_limit}"
        log_for_debugging(f"{name} {msg}")
        return EnvVarValidationResult(
            effective=upper_limit, status="capped", message=msg
        )
    return EnvVarValidationResult(effective=parsed, status="valid")
