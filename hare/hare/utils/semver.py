"""
Semver comparisons (loose). Port of src/utils/semver.ts.
"""

from __future__ import annotations

from typing import Any

_cmp: Any | None = None


def _semver_mod() -> Any:
    global _cmp
    if _cmp is None:
        try:
            from packaging.version import InvalidVersion, Version

            def cmp(a: str, b: str) -> int:
                try:
                    va, vb = Version(a), Version(b)
                except InvalidVersion:
                    return (a > b) - (a < b)
                if va < vb:
                    return -1
                if va > vb:
                    return 1
                return 0

            _cmp = cmp
        except ImportError:

            def _cmp(a, b):
                return (a > b) - (a < b)

    return _cmp


def gt(a: str, b: str) -> bool:
    return _semver_mod()(a, b) == 1


def gte(a: str, b: str) -> bool:
    return _semver_mod()(a, b) >= 0


def lt(a: str, b: str) -> bool:
    return _semver_mod()(a, b) == -1


def lte(a: str, b: str) -> bool:
    return _semver_mod()(a, b) <= 0


def order(a: str, b: str) -> int:
    return _semver_mod()(a, b)


def satisfies(version: str, range_spec: str) -> bool:
    try:
        from packaging.specifiers import SpecifierSet
        from packaging.version import Version

        return Version(version) in SpecifierSet(range_spec)
    except Exception:
        return False
