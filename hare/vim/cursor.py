"""Minimal Cursor for vim motions (stub; port targets src/utils/Cursor.js)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Cursor:
    """Subset of Ink/terminal Cursor used by vim operators."""

    text: str
    offset: int = 0
    measured_text: object | None = None

    def __post_init__(self) -> None:
        if self.measured_text is None:
            self.measured_text = self

    def equals(self, other: object) -> bool:
        return (
            isinstance(other, Cursor)
            and other.offset == self.offset
            and other.text == self.text
        )

    def left(self) -> Cursor:
        return Cursor(self.text, max(0, self.offset - 1))

    def right(self) -> Cursor:
        return Cursor(self.text, min(len(self.text), self.offset + 1))

    def down_logical_line(self) -> Cursor:
        rest = self.text[self.offset :]
        nl = rest.find("\n")
        if nl == -1:
            return self
        return Cursor(self.text, self.offset + nl + 1)

    def up_logical_line(self) -> Cursor:
        before = self.text[: self.offset]
        if "\n" not in before:
            return Cursor(self.text, 0)
        prev_nl = before.rfind("\n", 0, len(before) - 1)
        start = 0 if prev_nl == -1 else prev_nl + 1
        return Cursor(self.text, start)

    def start_of_logical_line(self) -> Cursor:
        before = self.text[: self.offset]
        nl = before.rfind("\n")
        return Cursor(self.text, 0 if nl == -1 else nl + 1)

    def end_of_logical_line(self) -> Cursor:
        rest = self.text[self.offset :]
        nl = rest.find("\n")
        end = len(self.text) if nl == -1 else self.offset + nl
        return Cursor(self.text, end)

    def first_non_blank_in_logical_line(self) -> Cursor:
        c = self.start_of_logical_line()
        i = c.offset
        while i < len(self.text) and self.text[i] in " \t":
            i += 1
        return Cursor(self.text, min(i, len(self.text)))

    def start_of_first_line(self) -> Cursor:
        return Cursor(self.text, 0)

    def start_of_last_line(self) -> Cursor:
        if not self.text:
            return self
        if self.text[-1] == "\n":
            lines = self.text[:-1].split("\n")
        else:
            lines = self.text.split("\n")
        if not lines:
            return Cursor(self.text, 0)
        off = sum(len(x) + 1 for x in lines[:-1])
        return Cursor(self.text, off)

    def go_to_line(self, n: int) -> Cursor:
        lines = self.text.split("\n")
        idx = max(0, min(n - 1, len(lines) - 1))
        off = sum(len(lines[i]) + 1 for i in range(idx))
        return Cursor(self.text, min(off, len(self.text)))

    def is_at_end(self) -> bool:
        return self.offset >= len(self.text)

    def get_position(self) -> tuple[int, int]:
        line = self.text.count("\n", 0, self.offset)
        return (0, line)

    def find_character(self, ch: str, find_type: str, count: int) -> int | None:
        _ = count
        slice_ = self.text[self.offset + 1 :]
        idx = slice_.find(ch)
        if idx == -1:
            return None
        return self.offset + 1 + idx

    def next_offset(self, off: int) -> int:
        return min(len(self.text), off + 1)

    def snap_out_of_image_ref(self, off: int, _which: str) -> int:
        return off

    # ── Wrapped-line (display) movement ──────────────────────────────────
    # gj / gk  move by screen rows, not logical lines.  When soft-wrapping
    # is absent these degrade gracefully to logical-line movement while
    # preserving the target column as closely as possible.

    def down(self) -> Cursor:
        """gj : move one display line down, preserving column."""
        rest = self.text[self.offset :]
        nl = rest.find("\n")
        if nl == -1:
            return self  # already on the last logical line

        # column offset within the current line
        col = self._column_within_line()

        # start of the next logical line
        target_line_start = self.offset + nl + 1
        next_nl = self.text.find("\n", target_line_start)
        line_end = next_nl if next_nl != -1 else len(self.text)
        line_len = line_end - target_line_start

        # clamp column to next line's visual length
        clamped = min(col, line_len)
        return Cursor(self.text, target_line_start + clamped)

    def up(self) -> Cursor:
        """gk : move one display line up, preserving column."""
        before = self.text[: self.offset]
        if "\n" not in before:
            return Cursor(self.text, 0)  # already on the first logical line

        col = self._column_within_line()

        # Position of the newline that ends the *previous* line (and starts
        # the current line).
        cur_line_start_nl = before.rfind("\n")
        cur_line_start = cur_line_start_nl + 1

        if cur_line_start_nl == 0:
            # The first character in before is the \n — we are at column 0
            # of a line after the very first one.  Previous line is the
            # first buffer line.
            prev_line_start = 0
            prev_line_end = 0  # the \n itself, so line_len = 0
        else:
            prev_nl = before.rfind("\n", 0, cur_line_start_nl)
            prev_line_start = 0 if prev_nl == -1 else prev_nl + 1
            prev_line_end = cur_line_start_nl

        line_len = max(0, prev_line_end - prev_line_start)
        clamped = min(col, line_len)
        return Cursor(self.text, prev_line_start + clamped)

    def _column_within_line(self) -> int:
        """Return the cursor's 0-based column offset from the start of
        the current logical line."""
        before = self.text[: self.offset]
        nl = before.rfind("\n")
        return self.offset - (nl + 1) if nl != -1 else self.offset

    # ── Vim word motions ────────────────────────────────────────────────
    # In Vim, a "word" (lowercase w/b/e) is either:
    #   1. A sequence of word characters (letters, digits, underscore)
    #   2. A sequence of non-blank, non-word characters (punctuation/symbols)
    # A "WORD" (uppercase W/B/E) is a sequence of non-whitespace characters.

    def next_vim_word(self) -> Cursor:
        """w: forward to start of next vim-word."""
        if self.is_at_end():
            return self
        pos = self.offset
        ch = self.text[pos] if pos < len(self.text) else ""
        if is_vim_word_char(ch):
            while pos < len(self.text) and is_vim_word_char(self.text[pos]):
                pos += 1
        elif is_vim_punctuation(ch):
            while pos < len(self.text) and is_vim_punctuation(self.text[pos]):
                pos += 1
        while pos < len(self.text) and is_vim_whitespace(self.text[pos]):
            pos += 1
        return Cursor(self.text, pos)

    def prev_vim_word(self) -> Cursor:
        """b: backward to start of previous vim-word."""
        if self.offset <= 0:
            return self
        pos = self.offset - 1
        while pos > 0 and is_vim_whitespace(self.text[pos]):
            pos -= 1
        if pos == 0 and is_vim_whitespace(self.text[0]):
            return Cursor(self.text, 0)
        ch = self.text[pos]
        if is_vim_word_char(ch):
            while pos > 0 and is_vim_word_char(self.text[pos - 1]):
                pos -= 1
        elif is_vim_punctuation(ch):
            while pos > 0 and is_vim_punctuation(self.text[pos - 1]):
                pos -= 1
        return Cursor(self.text, pos)

    def end_of_vim_word(self) -> Cursor:
        """e: forward to end of current/next vim-word."""
        if self.is_at_end():
            return self
        pos = self.offset + 1  # step off current character
        while pos < len(self.text) and is_vim_whitespace(self.text[pos]):
            pos += 1
        if pos >= len(self.text):
            return Cursor(self.text, len(self.text))
        ch = self.text[pos]
        if is_vim_word_char(ch):
            while pos + 1 < len(self.text) and is_vim_word_char(self.text[pos + 1]):
                pos += 1
        elif is_vim_punctuation(ch):
            while pos + 1 < len(self.text) and is_vim_punctuation(self.text[pos + 1]):
                pos += 1
        return Cursor(self.text, pos)

    def end_of_prev_vim_word(self) -> Cursor:
        """ge: backward to end of previous vim-word."""
        if self.offset <= 0:
            return self

        # Clamp cursor-beyond-end to last valid character
        pos = min(self.offset, len(self.text) - 1)
        orig_class = _char_vim_class(self.text[pos]) if pos < len(self.text) else ""

        pos -= 1  # step one left

        # skip whitespace backward — track whether we crossed any (a
        # whitespace crossing means we definitely left the original word)
        crossed_whitespace = False
        while pos >= 0 and is_vim_whitespace(self.text[pos]):
            pos -= 1
            crossed_whitespace = True
        if pos < 0:
            return Cursor(self.text, 0)

        cur_class = _char_vim_class(self.text[pos])

        if not crossed_whitespace and cur_class == orig_class and orig_class != "":
            # We stayed within the same word — rewind past it entirely.
            pos = _rewind_to_start(self.text, pos)
            pos -= 1  # step off the word
            while pos >= 0 and is_vim_whitespace(self.text[pos]):
                pos -= 1
            if pos < 0:
                return Cursor(self.text, 0)

        # Now pos is somewhere in the PREVIOUS word — go to its end
        pos = _rewind_to_start(self.text, pos)
        pos = _advance_to_vim_word_end(self.text, pos)

        return Cursor(self.text, pos)

    # ── Vim WORD motions (uppercase) ────────────────────────────────────

    def next_WORD(self) -> Cursor:
        """W: forward to start of next WORD (non-whitespace run)."""
        if self.is_at_end():
            return self
        pos = self.offset
        while pos < len(self.text) and not is_vim_whitespace(self.text[pos]):
            pos += 1
        while pos < len(self.text) and is_vim_whitespace(self.text[pos]):
            pos += 1
        return Cursor(self.text, pos)

    def prev_WORD(self) -> Cursor:
        """B: backward to start of previous WORD."""
        if self.offset <= 0:
            return self
        pos = self.offset
        # Clamp to last valid index (cursor may be one past the end)
        if pos >= len(self.text):
            pos = len(self.text) - 1
        # If we are on a WORD boundary (left char is whitespace), step off
        if pos > 0 and is_vim_whitespace(self.text[pos - 1]):
            pos -= 1
        # skip whitespace backward
        while pos > 0 and is_vim_whitespace(self.text[pos]):
            pos -= 1
        # move to start of this WORD
        if not is_vim_whitespace(self.text[pos]) if pos < len(self.text) else False:
            while pos > 0 and not is_vim_whitespace(self.text[pos - 1]):
                pos -= 1
        return Cursor(self.text, pos)

    def end_of_WORD(self) -> Cursor:
        """E: forward to end of current/next WORD."""
        if self.is_at_end():
            return self
        pos = self.offset
        # Check if we are already at end of a WORD
        at_end_of_word = (
            not is_vim_whitespace(self.text[pos])
            and (
                pos + 1 >= len(self.text)
                or is_vim_whitespace(self.text[pos + 1])
            )
        )
        if at_end_of_word:
            pos += 1  # step off, then recurse (via next WORD + end)
            # skip whitespace
            while pos < len(self.text) and is_vim_whitespace(self.text[pos]):
                pos += 1
            if pos >= len(self.text):
                return Cursor(self.text, len(self.text))
            # advance to end of this WORD
            while pos + 1 < len(self.text) and not is_vim_whitespace(self.text[pos + 1]):
                pos += 1
            return Cursor(self.text, pos)
        # skip whitespace to find start of next WORD
        while pos < len(self.text) and is_vim_whitespace(self.text[pos]):
            pos += 1
        if pos >= len(self.text):
            return Cursor(self.text, len(self.text))
        # advance to end of this WORD
        while pos + 1 < len(self.text) and not is_vim_whitespace(self.text[pos + 1]):
            pos += 1
        return Cursor(self.text, pos)


def is_vim_word_char(ch: str) -> bool:
    return ch.isalnum() or ch == "_"


def is_vim_whitespace(ch: str) -> bool:
    return ch in " \t\n\r"


def is_vim_punctuation(ch: str) -> bool:
    return not (is_vim_word_char(ch) or is_vim_whitespace(ch))


def _char_vim_class(ch: str) -> str:
    """Return 'word', 'punct', 'space', or '' for empty string."""
    if not ch:
        return ""
    if is_vim_word_char(ch):
        return "word"
    if is_vim_whitespace(ch):
        return "space"
    return "punct"


def _rewind_to_start(text: str, pos: int) -> int:
    """Move *pos* leftwards to the first character of its vim-word class."""
    if pos < 0 or pos >= len(text):
        return pos
    ch = text[pos]
    if is_vim_word_char(ch):
        while pos > 0 and is_vim_word_char(text[pos - 1]):
            pos -= 1
    elif is_vim_punctuation(ch):
        while pos > 0 and is_vim_punctuation(text[pos - 1]):
            pos -= 1
    return pos


def _advance_to_vim_word_end(text: str, pos: int) -> int:
    """Move *pos* rightwards to the last character of its vim-word class."""
    if pos >= len(text):
        return pos
    ch = text[pos]
    if is_vim_word_char(ch):
        while pos + 1 < len(text) and is_vim_word_char(text[pos + 1]):
            pos += 1
    elif is_vim_punctuation(ch):
        while pos + 1 < len(text) and is_vim_punctuation(text[pos + 1]):
            pos += 1
    return pos
