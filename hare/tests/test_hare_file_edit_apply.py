"""Differential unit tests for FileEditTool's apply semantics vs the TS reference.

Pins the byte-level result of `call()` against `applyEditToFile`
(src/tools/FileEditTool/utils.ts). The subtle one: when new_string is empty
(a deletion) and old_string does NOT end with a newline but appears followed by
one in the file, the reference strips that trailing newline too, so deleting a
whole line does not leave a blank line behind.
"""

from __future__ import annotations

import asyncio
import os
import tempfile

import pytest

import hare.tools_impl.FileEditTool.file_edit_tool as ed


def _run_edit(original: str, old_string: str, new_string: str, replace_all: bool = False) -> str:
    d = tempfile.mkdtemp()
    p = os.path.join(d, "f.txt")
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(original)
    ed.mark_file_read(p, content=original)
    res = asyncio.run(
        ed.call(p, old_string=old_string, new_string=new_string, replace_all=replace_all)
    )
    assert "error" not in res, res
    with open(p, "r", encoding="utf-8", newline="") as f:
        return f.read()


@pytest.mark.integration
def test_delete_whole_line_strips_trailing_newline():
    # TS applyEditToFile: new_string=='' and file contains old+'\n' -> strip it.
    assert _run_edit("a\nb\nc\n", "b", "") == "a\nc\n"


@pytest.mark.integration
def test_delete_without_following_newline_keeps_bytes():
    # old_string not followed by a newline anywhere -> plain removal, no strip.
    assert _run_edit("foo bar", "bar", "") == "foo "


@pytest.mark.integration
def test_delete_old_string_already_ending_in_newline():
    # old_string ends with '\n' -> strip branch is skipped, plain removal.
    assert _run_edit("a\nb\nc\n", "b\n", "") == "a\nc\n"


@pytest.mark.integration
def test_replace_all_delete_strips_each_trailing_newline():
    assert _run_edit("b\nb\nx\n", "b", "", replace_all=True) == "x\n"


@pytest.mark.integration
def test_non_empty_replacement_is_unaffected():
    assert _run_edit("a\nb\nc\n", "b", "B") == "a\nB\nc\n"


# --- Curly-quote matching (findActualString / preserveQuoteStyle) ---

LD = "“"  # left double curly quote
RD = "”"  # right double curly quote
LS = "‘"  # left single curly quote
RS = "’"  # right single curly quote


@pytest.mark.integration
def test_straight_quotes_match_curly_quotes_in_file():
    # File has curly quotes, model emits straight quotes: the reference matches
    # via quote normalization and applies the edit (and preserves curly style).
    original = f"say {LD}hello{RD} now\n"
    expected = f"say {LD}bye{RD} now\n"
    assert _run_edit(original, 'say "hello" now', 'say "bye" now') == expected


@pytest.mark.integration
def test_curly_single_quote_apostrophe_preserved_as_contraction():
    # File uses a curly apostrophe; straight ' in new_string between letters is a
    # contraction -> right single curly quote.
    original = f"it{RS}s here\n"
    expected = f"it{RS}s gone\n"
    assert _run_edit(original, "it's here", "it's gone") == expected
