"""Width-aware truncation (port of truncate.ts) — simplified without grapheme splitter."""

from __future__ import annotations


def _width(s: str) -> int:
    try:
        import wcwidth

        return wcwidth.wcswidth(s) or len(s)
    except ImportError:
        return len(s)


def truncate_to_width(text: str, max_width: int) -> str:
    if _width(text) <= max_width:
        return text
    if max_width <= 1:
        return "…"
    w = 0
    out = ""
    for ch in text:
        cw = _width(ch)
        if w + cw > max_width - 1:
            break
        out += ch
        w += cw
    return out + "…"


def truncate_path_middle(path: str, max_length: int) -> str:
    if _width(path) <= max_length:
        return path
    if max_length <= 0:
        return "…"
    if max_length < 5:
        return truncate_to_width(path, max_length)
    last_slash = path.rfind("/")
    filename = path[last_slash:] if last_slash >= 0 else path
    directory = path[:last_slash] if last_slash >= 0 else ""
    if _width(filename) >= max_length - 1:
        return truncate_to_width(path, max_length)
    avail = max_length - 1 - _width(filename)
    if avail <= 0:
        return truncate_to_width(filename, max_length)
    td = truncate_to_width(directory, avail).rstrip("…")
    return td + "…" + filename
