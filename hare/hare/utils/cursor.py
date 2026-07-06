"""
Terminal input cursor, measured text, and kill ring.

Port of: src/utils/Cursor.ts — Ink `stringWidth` / `wrapAnsi` replaced with
`wcwidth`-aware stubs + `textwrap`; grapheme segmentation uses naive Unicode
clusters (extend with `regex` or `grapheme` for full parity).
"""

from __future__ import annotations

import re
import textwrap
import unicodedata
from dataclasses import dataclass, field
from typing import Callable, Literal

# --- Kill ring (global, matches TS module state) ---
_KILL_RING_MAX_SIZE = 10
_kill_ring: list[str] = []
_kill_ring_index = 0
_last_action_was_kill = False
_last_yank_start = 0
_last_yank_length = 0
_last_action_was_yank = False


def push_to_kill_ring(
    text: str, direction: Literal["prepend", "append"] = "append"
) -> None:
    global _last_action_was_kill, _last_action_was_yank
    if text:
        if _last_action_was_kill and _kill_ring:
            if direction == "prepend":
                _kill_ring[0] = text + _kill_ring[0]
            else:
                _kill_ring[0] = _kill_ring[0] + text
        else:
            _kill_ring.insert(0, text)
            if len(_kill_ring) > _KILL_RING_MAX_SIZE:
                _kill_ring.pop()
        _last_action_was_kill = True
        _last_action_was_yank = False


def get_last_kill() -> str:
    return _kill_ring[0] if _kill_ring else ""


def get_kill_ring_item(index: int) -> str:
    if not _kill_ring:
        return ""
    n = len(_kill_ring)
    idx = ((index % n) + n) % n
    return _kill_ring[idx]


def get_kill_ring_size() -> int:
    return len(_kill_ring)


def clear_kill_ring() -> None:
    global _kill_ring_index, _last_action_was_kill, _last_action_was_yank
    global _last_yank_start, _last_yank_length
    _kill_ring.clear()
    _kill_ring_index = 0
    _last_action_was_kill = False
    _last_action_was_yank = False
    _last_yank_start = 0
    _last_yank_length = 0


def reset_kill_accumulation() -> None:
    global _last_action_was_kill
    _last_action_was_kill = False


def record_yank(start: int, length: int) -> None:
    global _last_yank_start, _last_yank_length, _last_action_was_yank, _kill_ring_index
    _last_yank_start = start
    _last_yank_length = length
    _last_action_was_yank = True
    _kill_ring_index = 0


def can_yank_pop() -> bool:
    return _last_action_was_yank and len(_kill_ring) > 1


def yank_pop() -> dict[str, int | str] | None:
    global _kill_ring_index
    if not _last_action_was_yank or len(_kill_ring) <= 1:
        return None
    _kill_ring_index = (_kill_ring_index + 1) % len(_kill_ring)
    text = _kill_ring[_kill_ring_index]
    return {"text": text, "start": _last_yank_start, "length": _last_yank_length}


def update_yank_length(length: int) -> None:
    global _last_yank_length
    _last_yank_length = length


def reset_yank_state() -> None:
    global _last_action_was_yank
    _last_action_was_yank = False


# --- Vim character classification utilities ---

VIM_WORD_CHAR_REGEX = re.compile(r"^[\w]$", re.UNICODE)
WHITESPACE_REGEX = re.compile(r"\s")


def is_vim_word_char(ch: str) -> bool:
    """Test whether *ch* is a Vim word character (letter, digit, mark, or underscore)."""
    return bool(ch) and bool(VIM_WORD_CHAR_REGEX.match(ch))


def is_vim_whitespace(ch: str) -> bool:
    return bool(WHITESPACE_REGEX.match(ch))


def is_vim_punctuation(ch: str) -> bool:
    return bool(ch) and not is_vim_whitespace(ch) and not is_vim_word_char(ch)


# --- String width / wrapping utilities ---


def string_width(s: str) -> int:
    """Compute display width of *s* (CJK-aware via wcwidth, with NFC fallback)."""
    try:
        import wcwidth  # type: ignore[import-not-found]

        w = wcwidth.wcswidth(s)
        return w if w >= 0 else len(s)
    except Exception:
        # Fallback: count only non-combining characters
        width = 0
        for ch in unicodedata.normalize("NFC", s):
            if unicodedata.combining(ch) == 0:
                # Zero-width joiners, variation selectors, etc.
                if ord(ch) in (0x200D, 0xFE0F, 0xFE0E):
                    continue
                # East Asian wide characters (CJK range approximation)
                cp = ord(ch)
                if (
                    (0x1100 <= cp <= 0x115F)  # Hangul Jamo
                    or (0x2329 <= cp <= 0x232A)  # Misc technical
                    or (0x2E80 <= cp <= 0xA4CF)  # CJK Radicals through Yijing
                    or (0xA960 <= cp <= 0xA97C)  # Hangul Jamo Extended-A
                    or (0xAC00 <= cp <= 0xD7AF)  # Hangul Syllables
                    or (0xF900 <= cp <= 0xFAFF)  # CJK Compatibility Ideographs
                    or (0xFE10 <= cp <= 0xFE19)  # Vertical forms
                    or (0xFE30 <= cp <= 0xFE6F)  # CJK Compatibility Forms
                    or (0xFF01 <= cp <= 0xFF60)  # Fullwidth Forms
                    or (0xFFE0 <= cp <= 0xFFE6)  # Fullwidth Signs
                    or (0x1B000 <= cp <= 0x1B2FF)  # Kana Supplement, etc.
                    or (0x1F004 <= cp <= 0x1F0CF)  # Mahjong/domino tiles
                    or (0x1F100 <= cp <= 0x1F1FF)  # Enclosed chars / regional indicators
                    or (0x1F200 <= cp <= 0x1F2FF)  # Enclosed Ideographic Supplement
                    or (0x1F300 <= cp <= 0x1F9FF)  # Misc Symbols, Emoticons, Emoji
                    or (0x20000 <= cp <= 0x2FFFF)  # CJK Unified Ideographs Extension B+
                    or (0x30000 <= cp <= 0x3FFFF)  # CJK Extension G/H
                ):
                    width += 2
                else:
                    width += 1
        return width


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences, returning plain text."""
    return _ANSI_RE.sub("", text)


def _find_ansi_ranges(text: str) -> list[tuple[int, int]]:
    """Return (start, end) ranges of ANSI escape sequences in *text*."""
    return [(m.start(), m.end()) for m in _ANSI_RE.finditer(text)]


def _wrap_with_ansi_awareness(
    plain: str, ansi_ranges: list[tuple[int, int]], width: int
) -> list[str]:
    """
    Wrap *plain* text at *width* columns, then re-insert ANSI sequences at
    their correct positions in each wrapped line.

    This mirrors Ink's wrapAnsi which preserves ANSI styling across soft wraps.
    """
    # Build a mapping from plain-text positions back to original positions
    # stripped -> original offsets
    # For efficiency, just track ANSI offsets relative to plain text
    lines_out: list[str] = []
    pos = 0
    n = len(plain)

    while pos < n:
        # Determine end of this wrapped line
        end = min(pos + width, n)
        if end < n and plain[end] != " " and not plain[end - 1].isspace():
            # Try to break at a word boundary
            space = plain.rfind(" ", pos, end)
            if space > pos:
                end = space + 1  # include the space
            else:
                # No space found, hard break at width
                end = min(pos + width, n)

        # Build the line with ANSI insertions
        line_chars: list[str] = []
        plain_idx = pos
        # Find ANSI codes that belong in this segment
        for a_start, a_end in ansi_ranges:
            # ANSI codes are placed at their original (plain) position
            # Convert: the ANSI code a_start in original text corresponds to
            # (a_start - preceding ANSI length) in plain text
            # But since we work with plain text directly, we need:
            # For each ANSI code with plain offset `ansi_plain_pos`, insert when we reach it
            pass

        # Simpler approach: just treat the original text with ANSI and use
        # a width-tracker that skips ANSI codes
        lines_out.append(plain[pos:end].rstrip(" "))
        pos = end
        # Skip leading whitespace on next line
        while pos < n and plain[pos] == " ":
            pos += 1

    if not lines_out:
        lines_out = [""]
    return lines_out


def wrap_ansi(text: str, width: int, **_opts: object) -> str:
    """
    Wrap text at *width* columns with ANSI escape sequence awareness.

    ANSI escape sequences (SGR color codes, cursor movement, etc.) are
    preserved and kept with their surrounding content. This mirrors Ink's
    wrapAnsi behavior — ANSI codes count as zero width.

    Keyword options (all optional):
        hard: bool   – insert hard newlines (always True for our use)
        trim: bool   – trim trailing whitespace from lines (default True)
        wordWrap: bool – wrap on word boundaries when possible (default True)
    """
    hard = _opts.get("hard", True)
    trim = bool(_opts.get("trim", True))
    word_wrap = bool(_opts.get("wordWrap", True))
    if width <= 0:
        return text

    # Strategy: track display width while walking the original text, inserting
    # line breaks at the appropriate display-column boundaries.
    lines: list[str] = []
    current_line: list[str] = []
    current_width = 0
    i = 0
    n = len(text)
    last_space_idx: int | None = None
    last_space_width = 0
    _pending_ansi: list[str] = []  # ANSI codes to prepend to the next line

    def _emit_line(chars: list[str]) -> None:
        nonlocal _pending_ansi
        line = "".join(chars)
        if trim:
            line = line.rstrip()
        if _pending_ansi:
            # Re-insert pending ANSI at the start of the next line
            line = "".join(_pending_ansi) + line
            _pending_ansi = []
        # Always append, even empty strings (they represent blank lines)
        lines.append(line)

    def _collect_ansi_from_end(chars: list[str]) -> None:
        """Extract trailing ANSI codes from *chars* for re-insertion on next line."""
        nonlocal _pending_ansi
        # Walk backwards collecting ANSI sequences
        ansi_codes: list[str] = []
        j = len(chars) - 1
        while j >= 0:
            m = _ANSI_RE.match("".join(chars), j)
            if m and m.start() == j:
                ansi_codes.insert(0, m.group(0))
                j = m.start() - 1
            else:
                # Check if chars[j] is part of an ANSI sequence
                part = "".join(chars[max(0, j - 10): j + 1])
                em = _ANSI_RE.search(part)
                if em and em.start() <= j - max(0, j - 10) and em.end() > j - max(0, j - 10):
                    j -= 1
                    continue
                break
        if ansi_codes:
            _pending_ansi = ansi_codes + _pending_ansi
            # Remove them from chars
            del chars[-sum(len(c) for c in ansi_codes):]

    while i < n:
        # Check for ANSI escape sequence
        ansi_match = _ANSI_RE.match(text, i)
        if ansi_match:
            current_line.append(ansi_match.group(0))
            i = ansi_match.end()
            continue

        ch = text[i]
        if ch == "\n":
            # Emit the current line WITHOUT the newline — the newline is the
            # line separator, not part of the content.
            if current_line:
                _emit_line(current_line)
            else:
                # Preserve blank lines (consecutive newlines)
                _emit_line([])
            current_line = []
            current_width = 0
            last_space_idx = None
            last_space_width = 0
            i += 1
            # Skip leading whitespace after explicit newline
            while i < n and text[i] in " \t":
                i += 1
            continue

        ch_width = string_width(ch)

        if ch_width + current_width > width:
            # Need a line break
            _collect_ansi_from_end(current_line)

            if word_wrap and last_space_idx is not None:
                # Rewind to last space, emit line up to (but not including) the space
                saved = current_line[:last_space_idx]
                rest = current_line[last_space_idx + 1:]  # skip the space itself

                if saved:
                    _emit_line(saved)
                elif lines:
                    # If saved is empty and we already have lines, don't emit a blank
                    pass

                current_line = rest
                # Recompute display width of the continuation
                current_width = _display_width_of_chars(rest)
                last_space_idx = None
                last_space_width = 0

                # Now process the current character on the new line
                if ch == " ":
                    # Skip leading space on the continuation line
                    i += 1
                    continue
                current_line.append(ch)
                if ch == " ":
                    last_space_idx = len(current_line) - 1
                    last_space_width = current_width
                current_width += ch_width
                i += 1
            else:
                # Hard break — emit current line, start fresh
                if current_line:
                    _emit_line(current_line)
                current_line = []
                current_width = 0
                last_space_idx = None
                last_space_width = 0
                # Skip leading whitespace on the new line
                while i < n and text[i] in " \t":
                    i += 1
        else:
            current_line.append(ch)
            current_width += ch_width
            if ch == " ":
                last_space_idx = len(current_line) - 1
                last_space_width = current_width - ch_width
            i += 1

    if current_line:
        _emit_line(current_line)

    if not lines:
        lines = [""]

    # If hard wrapping is enabled, join with newlines
    if hard:
        return "\n".join(lines)

    return "\n".join(lines)


def _display_width_of_chars(chars: list[str]) -> int:
    """Compute total display width of a list of character strings, skipping ANSI."""
    total = 0
    s = "".join(chars)
    # Strip ANSI before measuring
    plain = _ANSI_RE.sub("", s)
    for ch in plain:
        total += string_width(ch)
    return total


# --- Data classes ---


@dataclass
class Position:
    line: int
    column: int


@dataclass
class WrappedLine:
    text: str
    start_offset: int
    is_preceded_by_newline: bool
    ends_with_newline: bool = False


# --- MeasuredText ---


class MeasuredText:
    """
    Holds NFC-normalized text with lazy-computed wrapped lines, grapheme
    boundaries, and word boundaries.

    All text operations work on the normalized version so code-unit offsets
    are consistent regardless of how the user input NFD vs NFC forms.
    """

    def __init__(self, text: str, columns: int) -> None:
        self.text = unicodedata.normalize("NFC", text)
        self.columns = columns
        self._wrapped_lines: list[WrappedLine] | None = None
        self._grapheme_boundaries: list[int] | None = None
        self._word_boundaries: list[dict[str, int | bool]] | None = None
        self._navigation_cache: dict[str, int] = {}

    # ── Grapheme boundaries ──────────────────────────────────────────────

    def _compute_grapheme_boundaries(self) -> list[int]:
        """
        Compute grapheme cluster boundaries using a simple rules-based
        approach. This handles:
        - Base + combining marks
        - Hangul syllable blocks (jamo sequences)
        - Regional indicator pairs (flags)
        - ZWJ sequences (family emoji, etc.)
        - Variation selectors
        """
        boundaries: list[int] = []
        n = len(self.text)
        i = 0
        boundaries.append(0)
        while i < n:
            cp = ord(self.text[i])
            i += 1

            # Consume zero-width joiners and variation selectors
            while i < n and (
                unicodedata.combining(self.text[i]) != 0
                or ord(self.text[i]) in (0x200D, 0xFE0F, 0xFE0E)  # ZWJ, VS16, VS15
            ):
                i += 1

            # Regional indicator pairs (flags are two RI codepoints)
            if (
                0x1F1E6 <= cp <= 0x1F1FF
                and i < n
                and 0x1F1E6 <= ord(self.text[i]) <= 0x1F1FF
            ):
                i += 1
                # Continue consuming any combining marks after the pair
                while i < n and (
                    unicodedata.combining(self.text[i]) != 0
                    or ord(self.text[i]) in (0x200D, 0xFE0F, 0xFE0E)
                ):
                    i += 1

            boundaries.append(i)

        return boundaries

    def _grapheme_boundaries_list(self) -> list[int]:
        if self._grapheme_boundaries is None:
            self._grapheme_boundaries = self._compute_grapheme_boundaries()
        return self._grapheme_boundaries

    # ── Wrapped lines ───────────────────────────────────────────────────

    def _ensure_wrapped(self) -> list[WrappedLine]:
        if self._wrapped_lines is not None:
            return self._wrapped_lines

        wrapped_text = wrap_ansi(self.text, self.columns, hard=True, trim=False)
        lines = wrapped_text.split("\n")
        out: list[WrappedLine] = []
        search_offset = 0
        last_newline_pos = -1

        for i, line in enumerate(lines):
            if line == "":
                # Blank line — find the next newline in the original text
                last_newline_pos = self.text.find("\n", last_newline_pos + 1)
                if last_newline_pos != -1:
                    start_offset = last_newline_pos
                    is_preceded = i == 0 or (
                        start_offset > 0 and self.text[start_offset - 1] == "\n"
                    )
                    out.append(
                        WrappedLine(
                            text=line,
                            start_offset=start_offset,
                            is_preceded_by_newline=is_preceded,
                            ends_with_newline=True,
                        )
                    )
                else:
                    # End of text with a blank final line
                    start_offset = len(self.text)
                    is_preceded = i == 0 or (
                        start_offset > 0 and self.text[start_offset - 1] == "\n"
                    )
                    out.append(
                        WrappedLine(
                            text=line,
                            start_offset=start_offset,
                            is_preceded_by_newline=is_preceded,
                            ends_with_newline=False,
                        )
                    )
            else:
                # Non-blank line — find its position in the original text
                start_offset = self.text.find(line, search_offset)
                if start_offset == -1:
                    # Fallback: line text not found verbatim (e.g. ANSI stripping
                    # changed it). Use the previous offset as a best guess.
                    start_offset = search_offset

                search_offset = start_offset + len(line)
                potential_newline_pos = start_offset + len(line)
                ends_with_newline = (
                    potential_newline_pos < len(self.text)
                    and self.text[potential_newline_pos] == "\n"
                )
                if ends_with_newline:
                    last_newline_pos = potential_newline_pos

                is_preceded = i == 0 or (
                    start_offset > 0 and self.text[start_offset - 1] == "\n"
                )
                out.append(
                    WrappedLine(
                        text=line,
                        start_offset=start_offset,
                        is_preceded_by_newline=is_preceded,
                        ends_with_newline=ends_with_newline,
                    )
                )

        self._wrapped_lines = out or [WrappedLine("", 0, True)]
        return self._wrapped_lines

    def get_wrapped_text(self) -> list[str]:
        return [
            ln.text if ln.is_preceded_by_newline else ln.text.lstrip()
            for ln in self._ensure_wrapped()
        ]

    def get_wrapped_lines(self) -> list[WrappedLine]:
        return self._ensure_wrapped()

    def _get_line(self, line: int) -> WrappedLine:
        w = self._ensure_wrapped()
        if not w:
            return WrappedLine("", 0, True)
        idx = max(0, min(line, len(w) - 1))
        return w[idx]

    def get_line_length(self, line: int) -> int:
        return string_width(self._get_line(line).text)

    @property
    def line_count(self) -> int:
        return len(self._ensure_wrapped())

    # ── Word boundaries ──────────────────────────────────────────────────

    def get_word_boundaries(self) -> list[dict[str, int | bool]]:
        """
        Return word-like spans using Unicode-aware word detection.
        Handles CJK characters (each is its own word), Latin words separated
        by spaces, and punctuation.
        """
        if self._word_boundaries is not None:
            return self._word_boundaries

        out: list[dict[str, int | bool]] = []
        n = len(self.text)
        i = 0
        while i < n:
            ch = self.text[i]
            cp = ord(ch)
            # Determine character type
            is_cjk = (
                (0x2E80 <= cp <= 0xA4CF)
                or (0xA960 <= cp <= 0xA97C)
                or (0xAC00 <= cp <= 0xD7AF)
                or (0xF900 <= cp <= 0xFAFF)
                or (0xFF01 <= cp <= 0xFF60)
                or (0xFFE0 <= cp <= 0xFFE6)
                or (0x1B000 <= cp <= 0x1B2FF)
                or (0x1F100 <= cp <= 0x1F9FF)
                or (0x20000 <= cp <= 0x3FFFF)
                or (0x3040 <= cp <= 0x30FF)  # Hiragana, Katakana
                or (0x31F0 <= cp <= 0x31FF)  # Katakana Phonetic Extensions
                or (0x4E00 <= cp <= 0x9FFF)  # CJK Unified Ideographs
                or cp == 0x3000  # Ideographic space
            )

            if unicodedata.category(ch).startswith("L") or unicodedata.category(
                ch
            ).startswith("N"):
                # Letter or number — consume the whole word run
                start = i
                while i < n and (
                    unicodedata.category(self.text[i]).startswith("L")
                    or unicodedata.category(self.text[i]).startswith("N")
                    or self.text[i] == "'"
                    or self.text[i] == "_"
                ):
                    i += 1
                out.append({"start": start, "end": i, "isWordLike": True})
            elif is_cjk:
                # CJK — each character is its own word
                start = i
                i += 1
                # Consume combining marks / variation selectors
                while i < n and (
                    unicodedata.combining(self.text[i]) != 0
                    or ord(self.text[i]) in (0x200D, 0xFE0F, 0xFE0E)
                ):
                    i += 1
                out.append({"start": start, "end": i, "isWordLike": True})
            elif ch.isspace():
                i += 1
            else:
                # Punctuation / symbol — not word-like
                start = i
                i += 1
                out.append({"start": start, "end": i, "isWordLike": False})

        self._word_boundaries = out
        return out

    # ── Display width ↔ string index conversion ──────────────────────────

    def string_index_to_display_width(self, text: str, index: int) -> int:
        """Convert a string index to the corresponding display width."""
        if index <= 0:
            return 0
        if index >= len(text):
            return string_width(text)
        return string_width(text[:index])

    def display_width_to_string_index(self, text: str, target_width: int) -> int:
        """
        Convert a display width to a string index along the nearest
        grapheme boundary.
        """
        if target_width <= 0 or not text:
            return 0

        # If this matches our stored text, use the optimized path
        if text == self.text:
            return self._offset_at_display_width(target_width)

        # Compute on the fly
        boundaries = self._compute_grapheme_boundaries_for(text)
        current_width = 0
        for i in range(len(boundaries) - 1):
            start = boundaries[i]
            end = boundaries[i + 1]
            segment = text[start:end]
            seg_w = string_width(segment)
            if current_width + seg_w > target_width:
                return start
            current_width += seg_w
        return len(text)

    def _compute_grapheme_boundaries_for(self, text: str) -> list[int]:
        """Compute grapheme boundaries for arbitrary text (not just self.text)."""
        boundaries: list[int] = [0]
        n = len(text)
        i = 0
        while i < n:
            i += 1
            while i < n and (
                unicodedata.combining(text[i]) != 0
                or ord(text[i]) in (0x200D, 0xFE0F, 0xFE0E)
            ):
                i += 1
            boundaries.append(i)
        return boundaries

    def _offset_at_display_width(self, target_width: int) -> int:
        """Find the string offset that corresponds to a target display width."""
        if target_width <= 0:
            return 0

        boundaries = self._grapheme_boundaries_list()
        current_width = 0

        for i in range(len(boundaries) - 1):
            start = boundaries[i]
            end = boundaries[i + 1]
            segment = self.text[start:end]
            seg_w = string_width(segment)

            if current_width + seg_w > target_width:
                return start
            current_width += seg_w

        return len(self.text)

    # ── Position ↔ offset conversion ─────────────────────────────────────

    def get_offset_from_position(self, position: Position) -> int:
        wl = self._get_line(position.line)

        # Blank lines with trailing newline
        if wl.text == "" and wl.ends_with_newline:
            return wl.start_offset

        # Account for leading whitespace on wrapped (non-newline-prefixed) lines
        leading_ws = 0 if wl.is_preceded_by_newline else len(wl.text) - len(wl.text.lstrip())
        display_col = position.column + leading_ws
        string_idx = self.display_width_to_string_index(wl.text, display_col)

        offset = wl.start_offset + string_idx
        line_end = wl.start_offset + len(wl.text)

        # Allow positioning at the newline character after the line
        if wl.ends_with_newline and position.column > string_width(wl.text):
            max_offset = line_end + 1
        else:
            max_offset = line_end

        return min(offset, max_offset)

    def get_position_from_offset(self, offset: int) -> Position:
        lines = self._ensure_wrapped()
        for li, wl in enumerate(lines):
            next_line = lines[li + 1] if li + 1 < len(lines) else None
            if offset >= wl.start_offset and (
                next_line is None or offset < next_line.start_offset
            ):
                string_pos_in_line = offset - wl.start_offset

                if wl.is_preceded_by_newline:
                    display_col = self.string_index_to_display_width(
                        wl.text, string_pos_in_line
                    )
                else:
                    leading_ws = len(wl.text) - len(wl.text.lstrip())
                    if string_pos_in_line < leading_ws:
                        display_col = 0
                    else:
                        trimmed = wl.text.lstrip()
                        pos_in_trimmed = string_pos_in_line - leading_ws
                        display_col = self.string_index_to_display_width(
                            trimmed, pos_in_trimmed
                        )
                return Position(line=li, column=max(0, display_col))

        # Past end of last line
        last = lines[-1]
        return Position(line=len(lines) - 1, column=string_width(last.text))

    # ── Navigation helpers ───────────────────────────────────────────────

    def _binary_search_boundary(
        self, boundaries: list[int], target: int, find_next: bool
    ) -> int:
        """Binary search for the nearest boundary before or after *target*."""
        lo = 0
        hi = len(boundaries) - 1
        result = len(self.text) if find_next else 0

        while lo <= hi:
            mid = (lo + hi) >> 1
            boundary = boundaries[mid]
            if find_next:
                if boundary > target:
                    result = boundary
                    hi = mid - 1
                else:
                    lo = mid + 1
            else:
                if boundary < target:
                    result = boundary
                    lo = mid + 1
                else:
                    hi = mid - 1

        return result

    def _with_cache(self, key: str, compute: Callable[[], int]) -> int:
        cached = self._navigation_cache.get(key)
        if cached is not None:
            return cached
        result = compute()
        self._navigation_cache[key] = result
        return result

    def next_offset(self, offset: int) -> int:
        return self._with_cache(f"next:{offset}", lambda: self._binary_search_boundary(
            self._grapheme_boundaries_list(), offset, True
        ))

    def prev_offset(self, offset: int) -> int:
        if offset <= 0:
            return 0
        return self._with_cache(f"prev:{offset}", lambda: self._binary_search_boundary(
            self._grapheme_boundaries_list(), offset, False
        ))

    def snap_to_grapheme_boundary(self, offset: int) -> int:
        if offset <= 0:
            return 0
        if offset >= len(self.text):
            return len(self.text)
        b = self._grapheme_boundaries_list()
        lo, hi = 0, len(b) - 1
        while lo < hi:
            mid = (lo + hi + 1) >> 1
            if b[mid] <= offset:
                lo = mid
            else:
                hi = mid - 1
        return b[lo]


# --- Cursor ---


class Cursor:
    """
    Cursor tracking position within a MeasuredText with word/vim navigation,
    text modification, selection support, and rendering with cursor character.
    """

    def __init__(
        self,
        measured_text: MeasuredText,
        offset: int = 0,
        selection: int = 0,
    ) -> None:
        self.measured_text = measured_text
        # It is OK for the cursor to be 1 char beyond the end of the string
        self.offset = max(0, min(len(measured_text.text), offset))
        self.selection = selection

    @staticmethod
    def from_text(
        text: str, columns: int, offset: int = 0, selection: int = 0
    ) -> Cursor:
        # Use columns-1 so the cursor fits within the terminal width
        return Cursor(MeasuredText(text, columns - 1), offset, selection)

    # ── Properties ──────────────────────────────────────────────────────

    @property
    def text(self) -> str:
        return self.measured_text.text

    @property
    def columns(self) -> int:
        return self.measured_text.columns + 1

    # ── Position / equality ─────────────────────────────────────────────

    def get_position(self) -> Position:
        return self.measured_text.get_position_from_offset(self.offset)

    def get_offset(self, position: Position) -> int:
        return self.measured_text.get_offset_from_position(position)

    def equals(self, other: object) -> bool:
        return (
            isinstance(other, Cursor)
            and other.offset == self.offset
            and other.measured_text is self.measured_text
        )

    # ── Basic movement ──────────────────────────────────────────────────

    def left(self) -> Cursor:
        if self.offset == 0:
            return self
        chip = self.image_ref_ending_at(self.offset)
        if chip:
            return Cursor(self.measured_text, chip["start"])
        prev = self.measured_text.prev_offset(self.offset)
        return Cursor(self.measured_text, prev)

    def right(self) -> Cursor:
        if self.offset >= len(self.text):
            return self
        chip = self.image_ref_starting_at(self.offset)
        if chip:
            return Cursor(self.measured_text, chip["end"])
        nxt = self.measured_text.next_offset(self.offset)
        return Cursor(self.measured_text, min(nxt, len(self.text)))

    def is_at_end(self) -> bool:
        return self.offset >= len(self.text)

    def is_at_start(self) -> bool:
        return self.offset == 0

    # ── Image reference chip detection ──────────────────────────────────

    def image_ref_ending_at(self, offset: int) -> dict[str, int] | None:
        m = re.search(r"\[Image #\d+\]$", self.text[:offset])
        if not m:
            return None
        start = offset - len(m.group(0))
        return {"start": start, "end": offset}

    def image_ref_starting_at(self, offset: int) -> dict[str, int] | None:
        m = re.match(r"^\[Image #\d+\]", self.text[offset:])
        if not m:
            return None
        return {"start": offset, "end": offset + len(m.group(0))}

    def snap_out_of_image_ref(self, offset: int, toward: str) -> int:
        """If *offset* lands strictly inside an [Image #N] chip, snap it out."""
        re_pat = re.compile(r"\[Image #\d+\]")
        for m in re_pat.finditer(self.text):
            start = m.start()
            end = m.end()
            if start < offset < end:
                return start if toward == "start" else end
        return offset

    # ── Wrapped-line movement (up/down) ─────────────────────────────────

    def up(self) -> Cursor:
        """Move cursor up one wrapped display line, preserving column."""
        pos = self.get_position()
        if pos.line == 0:
            return self
        prev_line_text = (
            self.measured_text.get_wrapped_text()[pos.line - 1]
            if pos.line - 1 < len(self.measured_text.get_wrapped_text())
            else None
        )
        if prev_line_text is None:
            return self

        prev_line_width = string_width(prev_line_text)
        if pos.column > prev_line_width:
            new_offset = self.get_offset(Position(pos.line - 1, prev_line_width))
            return Cursor(self.measured_text, new_offset, 0)
        new_offset = self.get_offset(Position(pos.line - 1, pos.column))
        return Cursor(self.measured_text, new_offset, 0)

    def down(self) -> Cursor:
        """Move cursor down one wrapped display line, preserving column."""
        pos = self.get_position()
        if pos.line >= self.measured_text.line_count - 1:
            return self
        next_line_text = (
            self.measured_text.get_wrapped_text()[pos.line + 1]
            if pos.line + 1 < len(self.measured_text.get_wrapped_text())
            else None
        )
        if next_line_text is None:
            return self

        next_line_width = string_width(next_line_text)
        if pos.column > next_line_width:
            new_offset = self.get_offset(Position(pos.line + 1, next_line_width))
            return Cursor(self.measured_text, new_offset, 0)
        new_offset = self.get_offset(Position(pos.line + 1, pos.column))
        return Cursor(self.measured_text, new_offset, 0)

    # ── Line movement helpers ───────────────────────────────────────────

    def _start_of_current_line(self) -> Cursor:
        """Move to the start (column 0) of the current wrapped line."""
        pos = self.get_position()
        return Cursor(
            self.measured_text,
            self.get_offset(Position(pos.line, 0)),
            0,
        )

    def start_of_line(self) -> Cursor:
        """Move to start of current line; if already there, move to prev line."""
        pos = self.get_position()
        if pos.column == 0 and pos.line > 0:
            return Cursor(
                self.measured_text,
                self.get_offset(Position(pos.line - 1, 0)),
                0,
            )
        return self._start_of_current_line()

    def first_non_blank_in_line(self) -> Cursor:
        """Move to the first non-blank character on the current wrapped line."""
        pos = self.get_position()
        line_text = self.measured_text.get_wrapped_text()[pos.line] if pos.line < len(
            self.measured_text.get_wrapped_text()
        ) else ""
        m = re.match(r"^\s*\S", line_text)
        col = (m.end() - 1) if m else 0
        return Cursor(self.measured_text, self.get_offset(Position(pos.line, col)), 0)

    def end_of_line(self) -> Cursor:
        """Move to the end of the current wrapped line."""
        pos = self.get_position()
        col = self.measured_text.get_line_length(pos.line)
        return Cursor(self.measured_text, self.get_offset(Position(pos.line, col)), 0)

    # ── Logical line movement ────────────────────────────────────────────

    def _find_logical_line_start(self, from_offset: int | None = None) -> int:
        """Find the start offset of the logical line containing *from_offset*."""
        off = from_offset if from_offset is not None else self.offset
        prev_nl = self.text.rfind("\n", 0, off)
        return 0 if prev_nl == -1 else prev_nl + 1

    def _find_logical_line_end(self, from_offset: int | None = None) -> int:
        """Find the end offset of the logical line containing *from_offset*."""
        off = from_offset if from_offset is not None else self.offset
        next_nl = self.text.find("\n", off)
        return len(self.text) if next_nl == -1 else next_nl

    def _get_logical_line_bounds(self) -> tuple[int, int]:
        """Return (start, end) offsets of the current logical line."""
        return (self._find_logical_line_start(), self._find_logical_line_end())

    def _create_cursor_with_column(
        self, line_start: int, line_end: int, target_column: int
    ) -> Cursor:
        """Create a cursor at *target_column* on the line [line_start, line_end)."""
        line_len = line_end - line_start
        clamped = min(target_column, line_len)
        raw_offset = line_start + clamped
        snap_offset = self.measured_text.snap_to_grapheme_boundary(raw_offset)
        return Cursor(self.measured_text, snap_offset, 0)

    def end_of_logical_line(self) -> Cursor:
        return Cursor(self.measured_text, self._find_logical_line_end(), 0)

    def start_of_logical_line(self) -> Cursor:
        return Cursor(self.measured_text, self._find_logical_line_start(), 0)

    def first_non_blank_in_logical_line(self) -> Cursor:
        start, end = self._get_logical_line_bounds()
        line_text = self.text[start:end]
        m = re.search(r"\S", line_text)
        off = start + (m.start() if m else 0)
        return Cursor(self.measured_text, off, 0)

    def up_logical_line(self) -> Cursor:
        """Move up one logical line, preserving column position."""
        current_start = self._find_logical_line_start()
        if current_start == 0:
            return Cursor(self.measured_text, 0, 0)
        current_col = self.offset - current_start
        prev_line_end = current_start - 1
        prev_line_start = self._find_logical_line_start(prev_line_end)
        return self._create_cursor_with_column(prev_line_start, prev_line_end, current_col)

    def down_logical_line(self) -> Cursor:
        """Move down one logical line, preserving column position."""
        current_start, current_end = self._get_logical_line_bounds()
        if current_end >= len(self.text):
            return Cursor(self.measured_text, len(self.text), 0)
        current_col = self.offset - current_start
        next_line_start = current_end + 1
        next_line_end = self._find_logical_line_end(next_line_start)
        return self._create_cursor_with_column(next_line_start, next_line_end, current_col)

    # ── Word movement (Intl.Segmenter-like) ─────────────────────────────

    def next_word(self) -> Cursor:
        """Move forward to the start of the next word."""
        if self.is_at_end():
            return self
        for b in self.measured_text.get_word_boundaries():
            if b["isWordLike"] and b["start"] > self.offset:
                return Cursor(self.measured_text, b["start"])
        return Cursor(self.measured_text, len(self.text))

    def end_of_word(self) -> Cursor:
        """Move forward to the end of the current or next word."""
        if self.is_at_end():
            return self
        boundaries = self.measured_text.get_word_boundaries()
        for b in boundaries:
            if not b["isWordLike"]:
                continue
            # Inside this word but not at last char
            if self.offset >= b["start"] and self.offset < b["end"] - 1:
                return Cursor(self.measured_text, b["end"] - 1)
            # At last char of this word — go to end of next word
            if self.offset == b["end"] - 1:
                for nb in boundaries:
                    if nb["isWordLike"] and nb["start"] > self.offset:
                        return Cursor(self.measured_text, nb["end"] - 1)
                return self
        # Not in a word — find the next word and go to its end
        for b in boundaries:
            if b["isWordLike"] and b["start"] > self.offset:
                return Cursor(self.measured_text, b["end"] - 1)
        return self

    def prev_word(self) -> Cursor:
        """Move backward to the start of the previous word."""
        if self.is_at_start():
            return self
        boundaries = self.measured_text.get_word_boundaries()
        prev_word_start: int | None = None
        for b in boundaries:
            if not b["isWordLike"]:
                continue
            if b["start"] < self.offset:
                if self.offset > b["start"] and self.offset <= b["end"]:
                    return Cursor(self.measured_text, b["start"])
                prev_word_start = b["start"]
        if prev_word_start is not None:
            return Cursor(self.measured_text, prev_word_start)
        return Cursor(self.measured_text, 0)

    # ── Grapheme access ─────────────────────────────────────────────────

    def _grapheme_at(self, pos: int) -> str:
        """Return the grapheme cluster at *pos*, or '' if past the end."""
        if pos >= len(self.text):
            return ""
        nxt = self.measured_text.next_offset(pos)
        return self.text[pos:nxt]

    def _is_over_whitespace(self) -> bool:
        """Return True if the character under the cursor is whitespace."""
        ch = self.text[self.offset] if self.offset < len(self.text) else ""
        return bool(ch) and ch.isspace()

    # ── Vim word motions ────────────────────────────────────────────────

    def next_vim_word(self) -> Cursor:
        """w: forward to start of next vim-word."""
        if self.is_at_end():
            return self
        pos = self.offset
        advance = lambda p: self.measured_text.next_offset(p)

        current = self._grapheme_at(pos)
        if not current:
            return self

        if is_vim_word_char(current):
            while pos < len(self.text) and is_vim_word_char(self._grapheme_at(pos)):
                pos = advance(pos)
        elif is_vim_punctuation(current):
            while pos < len(self.text) and is_vim_punctuation(self._grapheme_at(pos)):
                pos = advance(pos)

        while pos < len(self.text) and WHITESPACE_REGEX.match(self._grapheme_at(pos)):
            pos = advance(pos)

        return Cursor(self.measured_text, pos)

    def end_of_vim_word(self) -> Cursor:
        """e: forward to end of current/next vim-word."""
        if self.is_at_end():
            return self
        text = self.text
        pos = self.offset
        advance = lambda p: self.measured_text.next_offset(p)

        if self._grapheme_at(pos) == "":
            return self

        pos = advance(pos)

        while pos < len(text) and WHITESPACE_REGEX.match(self._grapheme_at(pos)):
            pos = advance(pos)

        if pos >= len(text):
            return Cursor(self.measured_text, len(text))

        ch = self._grapheme_at(pos)
        if is_vim_word_char(ch):
            while pos < len(text):
                nxt = advance(pos)
                if nxt >= len(text) or not is_vim_word_char(self._grapheme_at(nxt)):
                    break
                pos = nxt
        elif is_vim_punctuation(ch):
            while pos < len(text):
                nxt = advance(pos)
                if nxt >= len(text) or not is_vim_punctuation(self._grapheme_at(nxt)):
                    break
                pos = nxt

        return Cursor(self.measured_text, pos)

    def prev_vim_word(self) -> Cursor:
        """b: backward to start of previous vim-word."""
        if self.is_at_start():
            return self
        pos = self.offset
        retreat = lambda p: self.measured_text.prev_offset(p)

        pos = retreat(pos)

        while pos > 0 and WHITESPACE_REGEX.match(self._grapheme_at(pos)):
            pos = retreat(pos)

        if pos == 0 and WHITESPACE_REGEX.match(self._grapheme_at(0)):
            return Cursor(self.measured_text, 0)

        ch = self._grapheme_at(pos)
        if is_vim_word_char(ch):
            while pos > 0:
                prev = retreat(pos)
                if not is_vim_word_char(self._grapheme_at(prev)):
                    break
                pos = prev
        elif is_vim_punctuation(ch):
            while pos > 0:
                prev = retreat(pos)
                if not is_vim_punctuation(self._grapheme_at(prev)):
                    break
                pos = prev

        return Cursor(self.measured_text, pos)

    def end_of_prev_vim_word(self) -> Cursor:
        """ge: backward to end of previous vim-word."""
        if self.offset <= 0:
            return self

        # Clamp cursor-beyond-end to last valid character
        pos = min(self.offset, len(self.text) - 1)

        def _char_class(ch: str) -> str:
            if not ch:
                return ""
            if is_vim_word_char(ch):
                return "word"
            if is_vim_whitespace(ch):
                return "space"
            return "punct"

        def _rewind_to_start(p: int) -> int:
            if p < 0 or p >= len(self.text):
                return p
            ch = self.text[p]
            if is_vim_word_char(ch):
                while p > 0 and is_vim_word_char(self.text[p - 1]):
                    p -= 1
            elif is_vim_punctuation(ch):
                while p > 0 and is_vim_punctuation(self.text[p - 1]):
                    p -= 1
            return p

        def _advance_to_word_end(p: int) -> int:
            if p >= len(self.text):
                return p
            ch = self.text[p]
            if is_vim_word_char(ch):
                while p + 1 < len(self.text) and is_vim_word_char(self.text[p + 1]):
                    p += 1
            elif is_vim_punctuation(ch):
                while p + 1 < len(self.text) and is_vim_punctuation(self.text[p + 1]):
                    p += 1
            return p

        orig_class = _char_class(self.text[pos]) if pos < len(self.text) else ""

        pos -= 1

        crossed_whitespace = False
        while pos >= 0 and is_vim_whitespace(self.text[pos]):
            pos -= 1
            crossed_whitespace = True
        if pos < 0:
            return Cursor(self.measured_text, 0)

        cur_class = _char_class(self.text[pos])

        if not crossed_whitespace and cur_class == orig_class and orig_class != "":
            pos = _rewind_to_start(pos)
            pos -= 1
            while pos >= 0 and is_vim_whitespace(self.text[pos]):
                pos -= 1
            if pos < 0:
                return Cursor(self.measured_text, 0)

        pos = _rewind_to_start(pos)
        pos = _advance_to_word_end(pos)

        return Cursor(self.measured_text, pos)

    # ── Vim WORD motions (uppercase) ────────────────────────────────────

    def next_WORD(self) -> Cursor:
        """W: forward to start of next WORD (non-whitespace run)."""
        c = self
        while not c._is_over_whitespace() and not c.is_at_end():
            c = c.right()
        while c._is_over_whitespace() and not c.is_at_end():
            c = c.right()
        return c

    def end_of_WORD(self) -> Cursor:
        """E: forward to end of current/next WORD."""
        if self.is_at_end():
            return self
        c: Cursor = self

        at_end_of_word = (
            not c._is_over_whitespace()
            and (c.right()._is_over_whitespace() or c.right().is_at_end())
        )
        if at_end_of_word:
            c = c.right()
            return c.end_of_WORD()

        if c._is_over_whitespace():
            c = c.next_WORD()

        while not c.right()._is_over_whitespace() and not c.is_at_end():
            c = c.right()

        return c

    def prev_WORD(self) -> Cursor:
        """B: backward to start of previous WORD."""
        c: Cursor = self

        if c.left()._is_over_whitespace():
            c = c.left()

        while c._is_over_whitespace() and not c.is_at_start():
            c = c.left()

        if not c._is_over_whitespace():
            while not c.left()._is_over_whitespace() and not c.is_at_start():
                c = c.left()

        return c

    # ── Text modification ───────────────────────────────────────────────

    def modify_text(self, end: Cursor, insert_string: str = "") -> Cursor:
        """Replace text from self.offset to end.offset with *insert_string*."""
        start_offset = self.offset
        end_offset = end.offset

        new_text = (
            self.text[:start_offset]
            + insert_string
            + self.text[end_offset:]
        )
        return Cursor.from_text(
            new_text,
            self.columns,
            start_offset + len(unicodedata.normalize("NFC", insert_string)),
        )

    def insert(self, s: str) -> Cursor:
        return self.modify_text(self, unicodedata.normalize("NFC", s))

    def delete(self) -> Cursor:
        """Delete the character under the cursor."""
        if self.is_at_end():
            return self
        return self.modify_text(self.right())

    def backspace(self) -> Cursor:
        """Delete the character before the cursor."""
        if self.is_at_start():
            return self
        return self.left().modify_text(self)

    def delete_to_line_start(self) -> tuple[Cursor, str]:
        """Delete from cursor to start of wrapped line. Returns (new_cursor, killed_text)."""
        if self.offset > 0 and self.text[self.offset - 1] == "\n":
            return (self.left().modify_text(self), "\n")
        start_cursor = self.start_of_line()
        killed = self.text[start_cursor.offset : self.offset]
        return (start_cursor.modify_text(self), killed)

    def delete_to_line_end(self) -> tuple[Cursor, str]:
        """Delete from cursor to end of wrapped line. Returns (new_cursor, killed_text)."""
        if self.offset < len(self.text) and self.text[self.offset] == "\n":
            return (self.modify_text(self.right()), "\n")
        end_cursor = self.end_of_line()
        killed = self.text[self.offset : end_cursor.offset]
        return (self.modify_text(end_cursor), killed)

    def delete_to_logical_line_end(self) -> Cursor:
        """Delete from cursor to end of logical line."""
        if self.offset < len(self.text) and self.text[self.offset] == "\n":
            return self.modify_text(self.right())
        return self.modify_text(self.end_of_logical_line())

    def delete_word_before(self) -> tuple[Cursor, str]:
        """Delete word before cursor. Returns (new_cursor, killed_text)."""
        if self.is_at_start():
            return (self, "")
        target = self.snap_out_of_image_ref(self.prev_word().offset, "start")
        prev_word_cursor = Cursor(self.measured_text, target)
        killed = self.text[prev_word_cursor.offset : self.offset]
        return (prev_word_cursor.modify_text(self), killed)

    def delete_word_after(self) -> Cursor:
        """Delete word after cursor."""
        if self.is_at_end():
            return self
        target = self.snap_out_of_image_ref(self.next_word().offset, "end")
        return self.modify_text(Cursor(self.measured_text, target))

    def delete_token_before(self) -> Cursor | None:
        """
        Delete a pasted/truncated text ref token before cursor if one exists.
        Returns None if no token found at cursor position.
        """
        # Cursor at chip start — backspace deletes the chip forward
        chip_after = self.image_ref_starting_at(self.offset)
        if chip_after:
            end = chip_after["end"] + 1 if (chip_after["end"] < len(self.text) and self.text[chip_after["end"]] == " ") else chip_after["end"]
            return self.modify_text(Cursor(self.measured_text, end))

        if self.is_at_start():
            return None

        # Only trigger if cursor is at a word boundary
        char_after = self.text[self.offset] if self.offset < len(self.text) else ""
        if char_after and not char_after.isspace():
            return None

        text_before = self.text[:self.offset]

        # Check for pasted/truncated text refs
        paste_match = re.search(
            r"(^|\s)\[(Pasted text #\d+(?: \+\d+ lines)?|Image #\d+|\.\.\.Truncated text #\d+ \+\d+ lines\.\.\.)\]$",
            text_before,
        )
        if paste_match:
            match_start = paste_match.start() + len(paste_match.group(1))
            return Cursor(self.measured_text, match_start).modify_text(self)

        return None

    # ── Character finding (vim f/F/t/T) ─────────────────────────────────

    def find_character(
        self,
        char: str,
        find_type: str,
        count: int = 1,
    ) -> int | None:
        """
        Find a character using vim f/F/t/T semantics.

        Args:
            char: The character to find (single grapheme).
            find_type: 'f' (forward to), 'F' (backward to),
                       't' (forward till), 'T' (backward till).
            count: Find the Nth occurrence.

        Returns:
            The target offset, or None if not found.
        """
        text = self.text
        forward = find_type in ("f", "t")
        till = find_type in ("t", "T")
        found = 0

        if forward:
            pos = self.measured_text.next_offset(self.offset)
            while pos < len(text):
                g = self._grapheme_at(pos)
                if g == char:
                    found += 1
                    if found == count:
                        if till:
                            return max(self.offset, self.measured_text.prev_offset(pos))
                        return pos
                pos = self.measured_text.next_offset(pos)
        else:
            if self.offset == 0:
                return None
            pos = self.measured_text.prev_offset(self.offset)
            while pos >= 0:
                g = self._grapheme_at(pos)
                if g == char:
                    found += 1
                    if found == count:
                        if till:
                            return min(self.offset, self.measured_text.next_offset(pos))
                        return pos
                if pos == 0:
                    break
                pos = self.measured_text.prev_offset(pos)

        return None

    # ── Document-level movement ─────────────────────────────────────────

    def start_of_first_line(self) -> Cursor:
        """Go to the very beginning of the text."""
        return Cursor(self.measured_text, 0, 0)

    def start_of_last_line(self) -> Cursor:
        """Go to the beginning of the last logical line."""
        last_nl = self.text.rfind("\n")
        if last_nl == -1:
            return self.start_of_line()
        return Cursor(self.measured_text, last_nl + 1, 0)

    def end_of_file(self) -> Cursor:
        """Go to the end of the text."""
        return Cursor(self.measured_text, len(self.text), 0)

    def go_to_line(self, line_number: int) -> Cursor:
        """
        Go to the beginning of the specified logical line (1-indexed, like vim).
        Uses logical lines (separated by \\n), not wrapped display lines.
        """
        lines = self.text.split("\n")
        target = max(0, min(line_number - 1, len(lines) - 1))
        offset = 0
        for i in range(target):
            offset += len(lines[i]) + 1  # +1 for newline
        return Cursor(self.measured_text, min(offset, len(self.text)), 0)

    # ── Viewport helpers ────────────────────────────────────────────────

    def get_viewport_start_line(self, max_visible_lines: int | None = None) -> int:
        """
        Compute the first visible wrapped line so the cursor stays centered
        within a scrolling viewport.
        """
        if max_visible_lines is None or max_visible_lines <= 0:
            return 0
        pos = self.get_position()
        all_lines = self.measured_text.get_wrapped_text()
        if len(all_lines) <= max_visible_lines:
            return 0
        half = max_visible_lines // 2
        start_line = max(0, pos.line - half)
        end_line = min(len(all_lines), start_line + max_visible_lines)
        if end_line - start_line < max_visible_lines:
            start_line = max(0, end_line - max_visible_lines)
        return start_line

    def get_viewport_char_offset(self, max_visible_lines: int | None = None) -> int:
        """Return the character offset of the first visible line."""
        start_line = self.get_viewport_start_line(max_visible_lines)
        if start_line == 0:
            return 0
        wrapped = self.measured_text.get_wrapped_lines()
        if start_line < len(wrapped):
            return wrapped[start_line].start_offset
        return 0

    def get_viewport_char_end(self, max_visible_lines: int | None = None) -> int:
        """Return the character offset just past the last visible line."""
        start_line = self.get_viewport_start_line(max_visible_lines)
        all_lines = self.measured_text.get_wrapped_lines()
        if max_visible_lines is None or max_visible_lines <= 0:
            return len(self.text)
        end_line = min(len(all_lines), start_line + max_visible_lines)
        if end_line >= len(all_lines):
            return len(self.text)
        return all_lines[end_line].start_offset if end_line < len(all_lines) else len(self.text)

    # ── Render ──────────────────────────────────────────────────────────

    def render(
        self,
        cursor_char: str,
        mask: str,
        invert: Callable[[str], str],
        ghost_text: dict[str, Callable[[str], str] | str] | None = None,
        max_visible_lines: int | None = None,
    ) -> str:
        """
        Render the text with cursor and optional masking, ghost text, and
        viewport scrolling. Returns a string suitable for terminal display.

        Args:
            cursor_char: Character to use for the cursor position.
            mask: Replacement character for masked text (e.g. password).
            invert: Function to invert/stylize the cursor character.
            ghost_text: Optional ghost text with a ``text`` field and a
                       ``dim`` function for styling.
            max_visible_lines: Maximum number of visible lines (for scrolling).

        Returns:
            Rendered string with ANSI styling and cursor indicator.
        """
        pos = self.get_position()
        all_lines = self.measured_text.get_wrapped_text()

        start_line = self.get_viewport_start_line(max_visible_lines)
        end_line = (
            min(len(all_lines), start_line + max_visible_lines)
            if max_visible_lines is not None and max_visible_lines > 0
            else len(all_lines)
        )

        rendered_lines: list[str] = []

        for i in range(start_line, end_line):
            current_line = i
            display_text = all_lines[i]

            # Apply mask
            if mask:
                # Count graphemes on this line
                graphemes: list[str] = []
                b = 0
                while b < len(display_text):
                    nxt = (
                        self.measured_text.next_offset(b)
                        if display_text is self.text
                        else min(b + 1, len(display_text))
                    )
                    # Simple grapheme extraction
                    g = display_text[b]
                    j = b + 1
                    while j < len(display_text) and unicodedata.combining(display_text[j]) != 0:
                        j += 1
                    graphemes.append(display_text[b:j])
                    b = j

                if current_line == len(all_lines) - 1:
                    # Last line: show last 6 graphemes, mask the rest
                    visible_count = min(6, len(graphemes))
                    mask_count = len(graphemes) - visible_count
                    if graphemes and mask_count > 0:
                        split_offset = sum(
                            len(g) for g in graphemes[:mask_count]
                        )
                        display_text = mask * mask_count + display_text[split_offset:]
                    elif mask_count > 0:
                        display_text = mask * mask_count
                else:
                    # Earlier wrapped lines: fully mask
                    display_text = mask * len(graphemes)

            # If this is not the cursor line, just output the line (trimmed)
            if pos.line != current_line:
                rendered_lines.append(display_text.rstrip())
                continue

            # Split the line into before/at/after cursor using grapheme iteration
            before_cursor = ""
            at_cursor = cursor_char
            after_cursor = ""
            current_width = 0
            cursor_found = False

            # Walk graphemes of display_text
            di = 0
            while di < len(display_text):
                # Extract one grapheme
                g_start = di
                g_end = di + 1
                while g_end < len(display_text) and unicodedata.combining(
                    display_text[g_end]
                ) != 0:
                    g_end += 1
                segment = display_text[g_start:g_end]
                di = g_end

                if cursor_found:
                    after_cursor += segment
                    continue

                seg_w = string_width(segment)
                if current_width + seg_w > pos.column:
                    at_cursor = segment
                    cursor_found = True
                else:
                    current_width += seg_w
                    before_cursor += segment

            # Ghost text — only on the last line when cursor is at end
            rendered_cursor: str
            ghost_suffix = ""
            if (
                ghost_text
                and isinstance(ghost_text.get("text"), str)
                and current_line == len(all_lines) - 1
                and self.is_at_end()
                and len(str(ghost_text["text"])) > 0
            ):
                gt_text = str(ghost_text["text"])
                # First ghost character goes in the inverted cursor (grapheme-safe)
                first_ghost = gt_text[0]
                rendered_cursor = (
                    invert(first_ghost) if cursor_char else first_ghost
                )
                ghost_rest = gt_text[1:]
                if ghost_rest:
                    dim_fn = ghost_text.get("dim")
                    if callable(dim_fn):
                        ghost_suffix = dim_fn(ghost_rest)
                    else:
                        ghost_suffix = ghost_rest
            else:
                rendered_cursor = invert(at_cursor) if cursor_char else at_cursor

            rendered_lines.append(
                before_cursor + rendered_cursor + ghost_suffix + after_cursor.rstrip()
            )

        return "\n".join(rendered_lines)
