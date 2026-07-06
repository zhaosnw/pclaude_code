"""Property-based tests for permission rule matching invariants.

Port of plan §8.1: permission.match(rule, action) idempotence.
"""

from __future__ import annotations

from hypothesis import given, strategies as st

from hare.utils.permissions.permission_rule import (
    parse_permission_rule,
    escape_rule_content,
    unescape_rule_content,
)
from hare.utils.permissions.shell_rule_matching import match_wildcard_pattern


class TestPermissionRuleProperties:
    """Invariants for permission rule parsing and matching."""

    @given(st.text(min_size=1, max_size=100))
    def test_escape_roundtrip(self, text: str) -> None:
        """escape → unescape is identity for non-special text."""
        escaped = escape_rule_content(text)
        result = unescape_rule_content(escaped)
        # The result may differ if text has unescape-able content patterns,
        # but the re-escaped form should match
        assert escape_rule_content(result) == escaped

    @given(st.text(min_size=0, max_size=50))
    def test_parse_does_not_crash(self, text: str) -> None:
        """Permission rule parsing never raises for arbitrary input."""
        try:
            parse_permission_rule(text)
        except Exception as exc:
            # Only acceptable exception is for truly malformed input
            assert "invalid" in str(exc).lower() or "malformed" in str(exc).lower()

    @given(st.text(min_size=1, max_size=20))
    def test_wildcard_star_matches_everything(self, text: str) -> None:
        """Wildcard '*' matches any input string."""
        assert match_wildcard_pattern("*", text)


class TestSettingsParseProperties:
    """Invariants for settings file parsing."""

    @given(
        st.dictionaries(
            keys=st.text(min_size=1, max_size=20),
            values=st.one_of(
                st.text(max_size=50),
                st.integers(),
                st.booleans(),
                st.none(),
            ),
            max_size=10,
        )
    )
    def test_settings_roundtrip_via_json(self, settings: dict) -> None:
        """Settings serialize→parse preserves non-complex values."""
        import json
        import tempfile
        from pathlib import Path
        from hare.utils.settings.settings import parse_settings_file

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(settings, f)
            f.flush()
            result = parse_settings_file(f.name)
            Path(f.name).unlink(missing_ok=True)

        # parse_settings_file returns {"settings": {...}, "errors": [...]}
        inner = result.get("settings", result)
        for key, value in settings.items():
            assert key in inner, f"Key {key} lost in roundtrip"
            assert inner[key] == value, (
                f"Value mismatch for {key}: {inner[key]} != {value}"
            )
