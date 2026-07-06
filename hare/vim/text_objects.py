"""Text objects (port of src/vim/textObjects.ts)."""

from __future__ import annotations

from hare.vim.cursor import is_vim_punctuation, is_vim_whitespace, is_vim_word_char

TextObjectRange = tuple[int, int] | None


def find_text_object(
    text: str, offset: int, object_type: str, is_inner: bool
) -> TextObjectRange:
    if object_type == "w":
        return _find_word_object(text, offset, is_inner, is_vim_word_char)
    if object_type == "W":
        return _find_word_object(
            text, offset, is_inner, lambda ch: not is_vim_whitespace(ch)
        )
    return None


def _find_word_object(
    text: str,
    offset: int,
    is_inner: bool,
    is_word_char,
) -> TextObjectRange:
    n = len(text)
    if n == 0:
        return None
    o = max(0, min(offset, n - 1))
    start = o
    end = o + 1
    if is_word_char(text[o]):
        while start > 0 and is_word_char(text[start - 1]):
            start -= 1
        while end < n and is_word_char(text[end]):
            end += 1
    elif is_vim_whitespace(text[o]):
        while start > 0 and is_vim_whitespace(text[start - 1]):
            start -= 1
        while end < n and is_vim_whitespace(text[end]):
            end += 1
        return (start, end)
    elif is_vim_punctuation(text[o]):
        while start > 0 and is_vim_punctuation(text[start - 1]):
            start -= 1
        while end < n and is_vim_punctuation(text[end]):
            end += 1
    if not is_inner:
        if end < n and is_vim_whitespace(text[end]):
            while end < n and is_vim_whitespace(text[end]):
                end += 1
        elif start > 0 and is_vim_whitespace(text[start - 1]):
            while start > 0 and is_vim_whitespace(text[start - 1]):
                start -= 1
    return (start, end)
