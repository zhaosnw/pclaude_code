"""
Unit tests for hare session and version configuration.

Port of: src/setup.ts behavior verification.
"""

from __future__ import annotations

import hare


# ---------------------------------------------------------------------------
# Package version
# ---------------------------------------------------------------------------


def test_package_has_version() -> None:
    assert hasattr(hare, "VERSION")
    assert isinstance(hare.VERSION, str)
    assert len(hare.VERSION) > 0


def test_version_is_semver_like() -> None:
    parts = hare.VERSION.split(".")
    assert len(parts) == 3
    for p in parts:
        int(p)  # should be parseable as int


def test_package_has_build_time() -> None:
    assert hasattr(hare, "BUILD_TIME")
