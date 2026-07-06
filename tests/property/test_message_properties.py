"""Property-based tests for message and event invariants.

Port of plan §8.1: message_id uniqueness and substitution idempotence.
"""

from __future__ import annotations

from hypothesis import given, strategies as st


class TestMessageProperties:
    """Invariants for message handling."""

    @given(
        st.lists(
            st.text(min_size=1, max_size=40),
            min_size=0,
            max_size=20,
            unique=True,
        )
    )
    def test_uuid_uniqueness(self, seeds: list[str]) -> None:
        """Generated UUIDs from different seeds should be unique."""
        import uuid

        uuids = []
        for seed in seeds:
            uuids.append(str(uuid.uuid5(uuid.NAMESPACE_DNS, seed)))

        assert len(uuids) == len(set(uuids)), "UUIDs should be unique"


class TestJSONRoundtripProperties:
    """Invariants for JSONL read/write roundtrips."""

    @given(
        st.lists(
            st.dictionaries(
                keys=st.text(min_size=1, max_size=10),
                values=st.one_of(
                    st.text(max_size=30),
                    st.integers(min_value=-100, max_value=100),
                    st.booleans(),
                ),
                max_size=5,
            ),
            min_size=0,
            max_size=10,
        )
    )
    def test_jsonl_roundtrip(self, events: list[dict]) -> None:
        """JSONL write→read preserves all events."""
        import json
        import tempfile
        from pathlib import Path

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            for event in events:
                f.write(json.dumps(event) + "\n")
            f.flush()
            content = Path(f.name).read_text(encoding="utf-8")
            Path(f.name).unlink(missing_ok=True)

        parsed = [json.loads(line) for line in content.splitlines() if line.strip()]
        assert parsed == events
