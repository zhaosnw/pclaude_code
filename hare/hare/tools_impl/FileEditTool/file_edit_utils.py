"""Helpers for applying edits and normalizing paths. Port of: src/tools/FileEditTool/utils.ts"""

from __future__ import annotations

import os
import re
from pathlib import Path

_TRAILING_WS_RE = re.compile(r"\s+$")
_LINE_SPLIT_RE = re.compile(r"(\r\n|\n|\r)")
_MARKDOWN_RE = re.compile(r"\.(md|mdx)$", re.IGNORECASE)


def normalize_edit_path(raw: str, cwd: Path) -> Path:
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = cwd / p
    return p.resolve()


def count_occurrences(haystack: str, needle: str) -> int:
    if not needle:
        return 0
    return haystack.count(needle)


# Claude can't output curly quotes, so the reference normalizes curly quotes to
# straight quotes when matching, and re-applies the file's curly style to the
# replacement. Port of normalizeQuotes/findActualString/preserveQuoteStyle from
# src/tools/FileEditTool/utils.ts.
LEFT_SINGLE_CURLY_QUOTE = "‘"  # ‘
RIGHT_SINGLE_CURLY_QUOTE = "’"  # ’
LEFT_DOUBLE_CURLY_QUOTE = "“"  # “
RIGHT_DOUBLE_CURLY_QUOTE = "”"  # ”


def normalize_quotes(s: str) -> str:
    return (
        s.replace(LEFT_SINGLE_CURLY_QUOTE, "'")
        .replace(RIGHT_SINGLE_CURLY_QUOTE, "'")
        .replace(LEFT_DOUBLE_CURLY_QUOTE, '"')
        .replace(RIGHT_DOUBLE_CURLY_QUOTE, '"')
    )


def find_actual_string(file_content: str, search_string: str) -> str | None:
    """Return the real substring in file_content that matches search_string,
    allowing curly<->straight quote differences. None if not found.

    normalize_quotes is a 1:1 (length-preserving) char substitution, so the index
    and length in the normalized text map straight back onto the original text.
    """
    if search_string in file_content:
        return search_string
    normalized_search = normalize_quotes(search_string)
    normalized_file = normalize_quotes(file_content)
    idx = normalized_file.find(normalized_search)
    if idx != -1:
        return file_content[idx : idx + len(search_string)]
    return None


def _is_opening_context(s: str, index: int) -> bool:
    if index == 0:
        return True
    prev = s[index - 1]
    return prev in (" ", "\t", "\n", "\r", "(", "[", "{", "—", "–")


def _apply_curly_double_quotes(s: str) -> str:
    out: list[str] = []
    for i, ch in enumerate(s):
        if ch == '"':
            out.append(
                LEFT_DOUBLE_CURLY_QUOTE
                if _is_opening_context(s, i)
                else RIGHT_DOUBLE_CURLY_QUOTE
            )
        else:
            out.append(ch)
    return "".join(out)


def _apply_curly_single_quotes(s: str) -> str:
    out: list[str] = []
    n = len(s)
    for i, ch in enumerate(s):
        if ch == "'":
            prev = s[i - 1] if i > 0 else None
            nxt = s[i + 1] if i < n - 1 else None
            # An apostrophe between two letters is a contraction, not a quote.
            if prev is not None and prev.isalpha() and nxt is not None and nxt.isalpha():
                out.append(RIGHT_SINGLE_CURLY_QUOTE)
            else:
                out.append(
                    LEFT_SINGLE_CURLY_QUOTE
                    if _is_opening_context(s, i)
                    else RIGHT_SINGLE_CURLY_QUOTE
                )
        else:
            out.append(ch)
    return "".join(out)


def preserve_quote_style(old_string: str, actual_old_string: str, new_string: str) -> str:
    """When old_string matched via quote normalization, apply the file's curly
    quote style to new_string so the edit preserves typography."""
    if old_string == actual_old_string:
        return new_string

    has_double = (
        LEFT_DOUBLE_CURLY_QUOTE in actual_old_string
        or RIGHT_DOUBLE_CURLY_QUOTE in actual_old_string
    )
    has_single = (
        LEFT_SINGLE_CURLY_QUOTE in actual_old_string
        or RIGHT_SINGLE_CURLY_QUOTE in actual_old_string
    )
    if not has_double and not has_single:
        return new_string

    result = new_string
    if has_double:
        result = _apply_curly_double_quotes(result)
    if has_single:
        result = _apply_curly_single_quotes(result)
    return result


def is_markdown_path(file_path: str) -> bool:
    return bool(_MARKDOWN_RE.search(file_path))


def strip_trailing_whitespace(s: str) -> str:
    """Strip trailing whitespace from each line, preserving line endings.
    Port of stripTrailingWhitespace (src/tools/FileEditTool/utils.ts)."""
    parts = _LINE_SPLIT_RE.split(s)
    out: list[str] = []
    for i, part in enumerate(parts):
        # Even indices are line content, odd indices are line endings.
        out.append(_TRAILING_WS_RE.sub("", part) if i % 2 == 0 else part)
    return "".join(out)


# Replacements to de-sanitize strings from Claude. Claude can't see any of these
# (sanitized in the API), so it emits the sanitized versions; restore them so the
# edit matches the file. Port of DESANITIZATIONS (src/tools/FileEditTool/utils.ts).
DESANITIZATIONS: dict[str, str] = {
    "<fnr>": "<function_results>",
    "<n>": "<name>",
    "</n>": "</name>",
    "<o>": "<output>",
    "</o>": "</output>",
    "<e>": "<error>",
    "</e>": "</error>",
    "<s>": "<system>",
    "</s>": "</system>",
    "<r>": "<result>",
    "</r>": "</result>",
    "< META_START >": "<META_START>",
    "< META_END >": "<META_END>",
    "< EOT >": "<EOT>",
    "< META >": "<META>",
    "< SOS >": "<SOS>",
    "\n\nH:": "\n\nHuman:",
    "\n\nA:": "\n\nAssistant:",
}


def _desanitize_match_string(match_string: str) -> tuple[str, list[tuple[str, str]]]:
    result = match_string
    applied: list[tuple[str, str]] = []
    for frm, to in DESANITIZATIONS.items():
        before = result
        result = result.replace(frm, to)
        if before != result:
            applied.append((frm, to))
    return result, applied


def normalize_file_edit_input(
    file_path: str, old_string: str, new_string: str
) -> tuple[str, str]:
    """Request-side normalization of a single edit. Port of normalizeFileEditInput
    (src/tools/FileEditTool/utils.ts) as applied by normalizeToolInput:

    - new_string: strip trailing whitespace per line, except for .md/.mdx where
      two trailing spaces are a hard line break.
    - old_string: if the literal string isn't in the file, try de-sanitizing it;
      if that matches, mirror the same replacements into new_string.

    Reads the file itself (ENOENT-safe) exactly like the reference; returns the
    (possibly) normalized (old_string, new_string).
    """
    is_markdown = is_markdown_path(file_path)
    normalized_new = new_string if is_markdown else strip_trailing_whitespace(new_string)

    try:
        full_path = os.path.expanduser(file_path)
        # Raw read (no CRLF normalization), matching readFileSyncCached in the ref.
        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            file_content = f.read()
    except OSError:
        # File doesn't exist yet (new file) or unreadable: keep old_string as-is.
        return old_string, normalized_new

    if old_string in file_content:
        return old_string, normalized_new

    desanitized_old, applied = _desanitize_match_string(old_string)
    if applied and desanitized_old in file_content:
        desanitized_new = normalized_new
        for frm, to in applied:
            desanitized_new = desanitized_new.replace(frm, to)
        return desanitized_old, desanitized_new

    return old_string, normalized_new
