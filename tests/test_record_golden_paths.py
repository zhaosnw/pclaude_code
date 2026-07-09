from pathlib import Path


def test_record_golden_accepts_legacy_and_canonical_fixture_paths():
    from scripts.record_golden import _resolve_hare_fixture_path

    repo = Path(__file__).resolve().parents[1]
    canonical = repo / "hare" / "alignment" / "fixtures" / "single_turn_hello.json"

    assert _resolve_hare_fixture_path(
        "alignment/fixtures/single_turn_hello.json"
    ) == canonical.resolve()
    assert _resolve_hare_fixture_path(
        "hare/alignment/fixtures/single_turn_hello.json"
    ) == canonical.resolve()
