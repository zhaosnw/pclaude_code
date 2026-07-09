"""Differential tests for request-side input normalization (normalizeToolInput).

The TS reference normalizes tool_use inputs before execution
(src/utils/api.ts normalizeToolInput). For file tools this means:
- FileEdit: new_string has trailing whitespace stripped per line (except .md/.mdx),
  and old/new_string are de-sanitized for tokens Claude can't see
  (`<fnr>` -> `<function_results>`, etc.).
- FileWrite: content has trailing whitespace stripped per line (except .md/.mdx).

These pin that hare's execution matches the reference's resulting file bytes.
"""

from __future__ import annotations

import asyncio
import os
import tempfile

import pytest

import hare.tools_impl.FileEditTool.file_edit_tool as ed
import hare.tools_impl.FileWriteTool.file_write_tool as wr


def _edit(name: str, original: str, old_string: str, new_string: str) -> str:
    d = tempfile.mkdtemp()
    p = os.path.join(d, name)
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(original)
    ed.mark_file_read(p, content=original)
    res = asyncio.run(ed.call(p, old_string=old_string, new_string=new_string))
    assert "error" not in res, res
    with open(p, "r", encoding="utf-8", newline="") as f:
        return f.read()


def _write(name: str, content: str) -> str:
    d = tempfile.mkdtemp()
    p = os.path.join(d, name)
    res = asyncio.run(wr.call(p, content=content))
    assert "error" not in res, res
    with open(p, "r", encoding="utf-8", newline="") as f:
        return f.read()


# --- FileEdit: stripTrailingWhitespace on new_string ---

@pytest.mark.integration
def test_edit_strips_trailing_whitespace_in_new_string():
    assert _edit("f.txt", "a\nb\nc\n", "b", "B   ") == "a\nB\nc\n"


@pytest.mark.integration
def test_edit_strips_trailing_whitespace_each_line():
    assert _edit("f.txt", "a\nb\nc\n", "b", "B   \nX\t") == "a\nB\nX\nc\n"


@pytest.mark.integration
def test_edit_markdown_keeps_trailing_spaces():
    # Markdown hard line break (two trailing spaces) must be preserved.
    assert _edit("f.md", "a\nb\nc\n", "b", "B  ") == "a\nB  \nc\n"


# --- FileEdit: de-sanitization of tokens Claude can't see ---

@pytest.mark.integration
def test_edit_desanitizes_old_string_to_match_file():
    original = "x <function_results> y\n"
    assert _edit("f.txt", original, "x <fnr> y", "z") == "z\n"


@pytest.mark.integration
def test_edit_mirrors_desanitization_into_new_string():
    original = "a <function_results> b\n"
    assert _edit("f.txt", original, "a <fnr> b", "c <fnr> d") == "c <function_results> d\n"


# --- FileWrite: stripTrailingWhitespace on content ---

@pytest.mark.integration
def test_write_strips_trailing_whitespace():
    assert _write("out.txt", "hello   \nworld\t\n") == "hello\nworld\n"


@pytest.mark.integration
def test_write_markdown_keeps_trailing_spaces():
    assert _write("out.md", "line  \n") == "line  \n"
