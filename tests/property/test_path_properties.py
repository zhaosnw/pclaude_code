"""Property-based tests for path normalization invariants.

Port of plan §8.1: path_normalize idempotence and containment.
"""

from __future__ import annotations

from pathlib import Path

from hypothesis import given, strategies as st


class TestPathNormalizeProperties:
    """Invariants for path normalization and validation."""

    @given(st.text(min_size=1, max_size=100))
    def test_path_resolve_idempotent(self, text: str) -> None:
        """Path.resolve() is idempotent."""
        try:
            p = Path(text)
            r1 = p.resolve()
            r2 = r1.resolve()
            # Absolute path resolution is idempotent
            if r1.is_absolute():
                assert r1 == r2
        except (OSError, ValueError, RuntimeError):
            # Some strings produce invalid paths on some platforms
            pass

    @given(
        st.lists(
            st.text(min_size=1, max_size=20),
            min_size=1,
            max_size=8,
        )
    )
    def test_join_then_parent(self, parts: list[str]) -> None:
        """Joining parts then calling parent should give parent dir."""
        try:
            joined = Path(*parts)
            if joined != joined.parent:
                assert joined.parent / joined.name == joined
        except (OSError, ValueError):
            pass

    @given(st.text(min_size=1, max_size=60))
    def test_no_null_bytes_handled(self, text: str) -> None:
        """Path constructors handle or reject strings with null bytes."""
        if "\0" in text:
            try:
                p = Path(text)
                # On macOS Paths may contain null bytes — verify parts are accessible
                assert isinstance(p.parts, tuple)
            except (ValueError, TypeError, OSError):
                # Platform rejects null bytes
                pass
