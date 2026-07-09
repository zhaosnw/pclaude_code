from pathlib import Path

import pytest


def test_record_golden_accepts_canonical_fixture_path():
    from scripts.record_golden import _resolve_hare_fixture_path

    repo = Path(__file__).resolve().parents[2]
    canonical = repo / "hare" / "alignment" / "fixtures" / "single_turn_hello.json"

    assert _resolve_hare_fixture_path(
        "hare/alignment/fixtures/single_turn_hello.json"
    ) == canonical.resolve()


def test_record_golden_rejects_legacy_fixture_path():
    from scripts.record_golden import _resolve_hare_fixture_path

    with pytest.raises(ValueError, match="hare/alignment"):
        _resolve_hare_fixture_path("alignment/fixtures/single_turn_hello.json")
