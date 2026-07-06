"""Runtime injection of rate-limit errors (tests). Port of: src/services/rateLimitMocking.ts"""

from __future__ import annotations


def check_mock_rate_limit_error() -> bool:
    return False


def is_mock_rate_limit_error(_error: BaseException) -> bool:
    return False
