"""
String utilities — formatting, truncation, line wrapping.

Port of: src/utils/stringUtils.ts
"""

from __future__ import annotations


def capitalize(s: str) -> str:
    """Capitalize the first letter of a string."""
    if not s:
        return s
    return s[0].upper() + s[1:]


def truncate(s: str, max_len: int, suffix: str = "...") -> str:
    """Truncate a string to a max length with suffix."""
    if len(s) <= max_len:
        return s
    return s[: max_len - len(suffix)] + suffix


def pluralize(count: int, singular: str, plural: str | None = None) -> str:
    """Pluralize a word based on count."""
    if count == 1:
        return singular
    return plural if plural else singular + "s"


def truncate_to_width(s: str, width: int, ellipsis: str = "…") -> str:
    """Truncate string to a display width (approximates grapheme width)."""
    if len(s) <= width:
        return s
    return s[: width - len(ellipsis)] + ellipsis


def strip_ansi(s: str) -> str:
    """Strip ANSI escape sequences from a string."""
    import re
    return re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', s)


def indent(text: str, prefix: str = "  ") -> str:
    """Add prefix to every non-empty line."""
    lines = text.split("\n")
    return "\n".join((prefix + line) if line.strip() else line for line in lines)


def word_wrap(text: str, width: int = 80) -> str:
    """Wrap text at word boundaries."""
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        if len(current) + len(word) + 1 <= width:
            current = (current + " " + word).strip()
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return "\n".join(lines)


def slugify(text: str) -> str:
    """Convert text to a URL-friendly slug."""
    import re
    text = text.lower().strip()
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[\s_]+', '-', text)
    return re.sub(r'-+', '-', text)


def format_count(count: int, unit: str = "") -> str:
    """Format a count with unit, using K/M suffixes for large numbers."""
    if count < 1000:
        return f"{count}{' ' + unit if unit else ''}"
    elif count < 1_000_000:
        val = count / 1000
        return f"{val:.1f}K{' ' + pluralize(int(count/1000), unit) if unit else ''}"
    else:
        val = count / 1_000_000
        return f"{val:.1f}M{' ' + pluralize(int(count/1000000), unit) if unit else ''}"


def format_duration(ms: float) -> str:
    """Format a duration in milliseconds to a human-readable string."""
    if ms < 1000:
        return f"{int(ms)}ms"
    elif ms < 60_000:
        return f"{ms/1000:.1f}s"
    elif ms < 3_600_000:
        minutes = ms / 60_000
        seconds = (ms % 60_000) / 1000
        return f"{int(minutes)}m {int(seconds)}s"
    else:
        hours = ms / 3_600_000
        minutes = (ms % 3_600_000) / 60_000
        return f"{hours:.1f}h {int(minutes)}m"


def format_bytes(n: int) -> str:
    """Format bytes to human-readable size."""
    if n < 1024:
        return f"{n} B"
    elif n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    elif n < 1024 * 1024 * 1024:
        return f"{n / (1024*1024):.1f} MB"
    else:
        return f"{n / (1024*1024*1024):.2f} GB"
