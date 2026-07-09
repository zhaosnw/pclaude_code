"""Unit tests for the E2E file-effects comparator.

The golden CLI E2E (test_e2e_cases.py) historically only asserted stdout/exit
code, so a Write/Edit case that printed the right words but mutated the file
wrongly (or not at all) still passed. This pins the comparator that closes that
gap: snapshots of the sandbox after the run are diffed against the golden's
recorded file snapshot.

Snapshot entries are {path, sha256, text} where `text` is the decoded file
content with each side's own sandbox root already scrubbed to <SANDBOX> at
capture time, so the comparison is an environment-independent equality.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HARE_ALIGNMENT = REPO / "alignment"
sys.path.insert(0, str(HARE_ALIGNMENT))

from golden_normalize import compare_file_effects  # noqa: E402


def _entry(path: str, text: str) -> dict:
    return {"path": path, "text": text, "sha256": f"sha-of-{text}"}


def test_identical_snapshots_match():
    files = [_entry("out.txt", "hello\n")]
    assert compare_file_effects(files, files) is None


def test_missing_file_is_a_mismatch():
    actual: list[dict] = []
    golden = [_entry("out.txt", "hello\n")]
    diff = compare_file_effects(actual, golden)
    assert diff is not None
    assert "out.txt" in diff


def test_wrong_content_is_a_mismatch():
    actual = [_entry("out.txt", "WRONG\n")]
    golden = [_entry("out.txt", "hello\n")]
    diff = compare_file_effects(actual, golden)
    assert diff is not None
    assert "out.txt" in diff


def test_extra_unexpected_file_is_a_mismatch():
    actual = [_entry("out.txt", "hello\n"), _entry("junk.txt", "x\n")]
    golden = [_entry("out.txt", "hello\n")]
    diff = compare_file_effects(actual, golden)
    assert diff is not None
    assert "junk.txt" in diff


def test_binary_falls_back_to_sha256():
    # text is None (undecodable) -> compare by sha256
    actual = [{"path": "img.bin", "text": None, "sha256": "aaa"}]
    golden = [{"path": "img.bin", "text": None, "sha256": "bbb"}]
    assert compare_file_effects(actual, golden) is not None
    same = [{"path": "img.bin", "text": None, "sha256": "aaa"}]
    assert compare_file_effects(same, same) is None
