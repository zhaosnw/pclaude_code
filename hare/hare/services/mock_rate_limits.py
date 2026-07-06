"""Test doubles for rate limit errors. Port of: src/services/mockRateLimits.ts"""

from __future__ import annotations


def is_mock_rate_limit_enabled() -> bool:
    return False
