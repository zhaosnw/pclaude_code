from pathlib import Path
import json

import pytest


def test_record_golden_accepts_canonical_fixture_path():
    from scripts.record_golden import _resolve_hare_fixture_path

    repo = Path(__file__).resolve().parents[1]
    canonical = repo / "hare" / "alignment" / "fixtures" / "single_turn_hello.json"

    assert _resolve_hare_fixture_path(
        "hare/alignment/fixtures/single_turn_hello.json"
    ) == canonical.resolve()


def test_record_golden_rejects_legacy_fixture_path():
    from scripts.record_golden import _resolve_hare_fixture_path

    with pytest.raises(ValueError, match="hare/alignment"):
        _resolve_hare_fixture_path("alignment/fixtures/single_turn_hello.json")


def test_record_golden_reads_newest_persisted_session_id(tmp_path):
    from scripts.record_golden import _session_id_from_config_dir

    sessions = tmp_path / "sessions"
    sessions.mkdir()
    old = sessions / "old.json"
    old.write_text(json.dumps({"sessionId": "old-session"}), encoding="utf-8")
    new = sessions / "new.json"
    new.write_text(json.dumps({"sessionId": "new-session"}), encoding="utf-8")

    assert _session_id_from_config_dir(tmp_path) == "new-session"
