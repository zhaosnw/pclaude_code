"""
Tool error formatting.

Port of: src/utils/toolErrors.ts
"""

from __future__ import annotations


def format_error(error: Exception | str) -> str:
    if isinstance(error, str):
        return error
    return str(error)
