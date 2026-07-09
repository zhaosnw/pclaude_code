"""
Unit tests for hare.history — paste/image references, parsing.

Port of: src/history.ts behavior verification.
"""

from __future__ import annotations

from hare.history import (
    format_image_ref,
    format_pasted_text_ref,
    get_pasted_text_ref_num_lines,
    parse_references,
)


# ---------------------------------------------------------------------------
# get_pasted_text_ref_num_lines
# ---------------------------------------------------------------------------


def test_num_lines_empty() -> None:
    assert get_pasted_text_ref_num_lines("") == 0


def test_num_lines_single_line() -> None:
    assert get_pasted_text_ref_num_lines("hello") == 0


def test_num_lines_with_newlines() -> None:
    assert get_pasted_text_ref_num_lines("line1\nline2\nline3") == 2


def test_num_lines_with_carriage_return() -> None:
    assert get_pasted_text_ref_num_lines("a\r\nb") == 1


# ---------------------------------------------------------------------------
# format_pasted_text_ref
# ---------------------------------------------------------------------------


def test_format_pasted_text_empty() -> None:
    result = format_pasted_text_ref(1, 0)
    assert result == "[Pasted text #1]"


def test_format_pasted_text_with_lines() -> None:
    result = format_pasted_text_ref(2, 5)
    assert result == "[Pasted text #2 +5 lines]"


# ---------------------------------------------------------------------------
# format_image_ref
# ---------------------------------------------------------------------------


def test_format_image_ref() -> None:
    assert format_image_ref(3) == "[Image #3]"


# ---------------------------------------------------------------------------
# parse_references
# ---------------------------------------------------------------------------


def test_parse_references_empty() -> None:
    assert parse_references("") == []


def test_parse_references_no_refs() -> None:
    assert parse_references("hello world") == []


def test_parse_references_pasted_text() -> None:
    refs = parse_references("[Pasted text #1]")
    assert len(refs) == 1
    assert refs[0]["id"] == 1
    assert "Pasted text" in str(refs[0]["match"])


def test_parse_references_pasted_text_with_lines() -> None:
    refs = parse_references("[Pasted text #2 +10 lines]")
    assert len(refs) == 1
    assert refs[0]["id"] == 2


def test_parse_references_image() -> None:
    refs = parse_references("[Image #5]")
    assert len(refs) == 1
    assert refs[0]["id"] == 5


def test_parse_references_truncated() -> None:
    refs = parse_references("[...Truncated text #3]")
    assert len(refs) == 1
    assert refs[0]["id"] == 3


def test_parse_references_multiple() -> None:
    text = "Here [Pasted text #1] and [Image #2] and [Pasted text #3 +5 lines]"
    refs = parse_references(text)
    assert len(refs) == 3
    ids = [r["id"] for r in refs]
    assert ids == [1, 2, 3]


def test_parse_references_ignores_id_zero() -> None:
    refs = parse_references("[Pasted text #0]")
    assert refs == []


def test_parse_references_has_index() -> None:
    text = "prefix [Pasted text #1] suffix"
    refs = parse_references(text)
    assert refs[0]["index"] == 7  # position where match starts
