"""Terminal text wrapping / truncation (port of terminal.ts)."""

from __future__ import annotations

MAX_LINES_TO_SHOW = 3
PADDING = 10


def _string_width(s: str) -> int:
    try:
        import wcwidth

        return wcwidth.wcswidth(s) or len(s)
    except ImportError:
        return len(s)


def _wrap_text(text: str, wrap_width: int) -> tuple[str, int]:
    lines = text.split("\n")
    wrapped: list[str] = []
    for line in lines:
        w = _string_width(line)
        if w <= wrap_width:
            wrapped.append(line.rstrip())
        else:
            pos = 0
            while pos < w:
                chunk = line[pos : pos + wrap_width]
                wrapped.append(chunk.rstrip())
                pos += wrap_width
    remaining = len(wrapped) - MAX_LINES_TO_SHOW
    if remaining == 1:
        return "\n".join(wrapped[: MAX_LINES_TO_SHOW + 1]).rstrip(), 0
    return "\n".join(wrapped[:MAX_LINES_TO_SHOW]).rstrip(), max(0, remaining)


def render_truncated_content(
    content: str,
    terminal_width: int,
    suppress_expand_hint: bool = False,
) -> str:
    trimmed = content.rstrip()
    if not trimmed:
        return ""
    wrap_width = max(terminal_width - PADDING, 10)
    max_chars = MAX_LINES_TO_SHOW * wrap_width * 4
    pre_truncated = len(trimmed) > max_chars
    content_for_wrapping = trimmed[:max_chars] if pre_truncated else trimmed
    above, remaining = _wrap_text(content_for_wrapping, wrap_width)
    estimated = remaining
    if pre_truncated:
        estimated = max(
            estimated,
            (len(trimmed) + wrap_width - 1) // wrap_width - MAX_LINES_TO_SHOW,
        )
    hint = "" if suppress_expand_hint else " (ctrl+o to expand)"
    suffix = f"\n… +{estimated} lines{hint}" if estimated > 0 else ""
    return "\n".join([x for x in (above, suffix.strip()) if x])


def is_output_line_truncated(content: str) -> bool:
    pos = 0
    for _ in range(MAX_LINES_TO_SHOW + 1):
        pos = content.find("\n", pos)
        if pos == -1:
            return False
        pos += 1
    return pos < len(content)
