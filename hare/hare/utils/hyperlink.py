"""OSC 8 terminal hyperlinks — port of `frontend/src/utils/hyperlink.ts`.

OSC 8 escape sequences let terminals render clickable links.  Format::

    ESC ] 8 ; ; URL BEL TEXT ESC ] 8 ; ; BEL

``BEL`` (``\\x07``) is the terminator (more widely supported than ``ST``).

Reference: https://gist.github.com/egmontkob/eb114294efbcd5adb1944c9f3cb5feda
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from typing import TYPE_CHECKING, NamedTuple
from urllib.parse import quote, urlparse

if TYPE_CHECKING:
    from collections.abc import Mapping

# ---------------------------------------------------------------------------
# OSC 8 escape constants
# ---------------------------------------------------------------------------

OSC8_START = "\x1b]8;;"
OSC8_END = "\x07"

# ANSI blue colour (used when rendering link text for hyperlink-capable terminals)
_ANSI_BLUE = "\x1b[34m"
_ANSI_RESET = "\x1b[0m"

# ---------------------------------------------------------------------------
# Regex for stripping OSC 8 sequences from output
# ---------------------------------------------------------------------------

_OSC8_RE = re.compile(r"\x1b\]8;;(?:[^\x07]*)\x07")

# Regex that captures a full OSC 8 open (with optional id) + text + close
_OSC8_FULL_RE = re.compile(
    r"\x1b\]8;(?:id=(?P<id>[^;]*))?;"
    r"(?P<url>[^\x07]*)\x07"
    r"(?P<text>.*?)"
    r"\x1b\]8;;\x07",
    re.DOTALL,
)

# ---------------------------------------------------------------------------
# Terminals known to support OSC 8 that aren't auto-detected by the
# ``supports-hyperlinks`` heuristics.
# ---------------------------------------------------------------------------

_ADDITIONAL_HYPERLINK_TERMINALS: tuple[str, ...] = (
    "ghostty",
    "Hyper",
    "kitty",
    "alacritty",
    "iTerm.app",
    "iTerm2",
)

# ---------------------------------------------------------------------------
# Structured result type
# ---------------------------------------------------------------------------


class Osc8Parsed(NamedTuple):
    """Result of parsing a single OSC 8 hyperlink sequence."""

    url: str
    """The URL target."""
    text: str
    """The visible link text (may include ANSI formatting)."""
    link_id: str | None
    """Optional OSC 8 ``id`` parameter for link grouping."""
    start: int
    """Byte offset of the OSC 8 start sequence within the source text."""
    end: int
    """Byte offset immediately after the OSC 8 close sequence."""


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def supports_hyperlinks(
    env: Mapping[str, str] | None = None,
    *,
    _stdout_supported: bool | None = None,
) -> bool:
    """Return *True* when the current terminal is likely to support OSC 8.

    Detection uses the same strategy as the TypeScript frontend:

    1. If *stdout is known to support hyperlinks* (e.g. via the
       ``supports-hyperlinks`` npm package), return immediately.
    2. Consult ``TERM_PROGRAM``, ``LC_TERMINAL``, and ``TERM`` environment
       variables against a list of known-good terminals.

    Parameters
    ----------
    env:
        Environment dict override (defaults to ``os.environ``).  Useful for
        testing or for reading a *different* process's environment.
    _stdout_supported:
        Pre-computed stdout-support flag (for callers that already queried
        the ``supports-hyperlinks`` package / ``isatty`` check).  Not part of
        the public API; use ``env`` for production overrides.
    """
    if _stdout_supported:
        return True

    _env = dict(env if env is not None else os.environ)

    term_program = _env.get("TERM_PROGRAM", "")
    if term_program and term_program in _ADDITIONAL_HYPERLINK_TERMINALS:
        return True

    lc_terminal = _env.get("LC_TERMINAL", "")
    if lc_terminal and lc_terminal in _ADDITIONAL_HYPERLINK_TERMINALS:
        return True

    term = _env.get("TERM", "")
    if "kitty" in term:
        return True

    if _env.get("WT_SESSION", ""):
        return True

    if _env.get("VTE_VERSION", ""):
        return True

    return False


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------


def is_url(candidate: str) -> bool:
    """Return *True* when *candidate* looks like a well-formed URL.

    Checks for a recognised scheme (``http``, ``https``, ``file``,
    ``mailto``) with a non-empty netloc or path.  Does **not** perform a
    full RFC 3986 validation — it answers "would this be a reasonable thing
    to put inside an OSC 8 sequence?".

        >>> is_url("https://example.com/path")
        True
        >>> is_url("not-a-url")
        False
    """
    try:
        parsed = urlparse(candidate)
    except (ValueError, AttributeError):
        return False
    if parsed.scheme not in ("http", "https", "file", "mailto"):
        return False
    if parsed.scheme == "mailto":
        return bool(parsed.path)
    return bool(parsed.netloc) or bool(parsed.path)


def _maybe_hyperlink(
    url: str,
    content: str | None,
    has_support: bool,
) -> str:
    """Build an OSC 8 hyperlink string or fall back to the plain URL."""
    if not has_support:
        return url
    display = content if content is not None else url
    coloured = f"{_ANSI_BLUE}{display}{_ANSI_RESET}"
    return f"{OSC8_START}{url}{OSC8_END}{coloured}{OSC8_START}{OSC8_END}"


# ---------------------------------------------------------------------------
# Core hyperlink builders
# ---------------------------------------------------------------------------


def create_hyperlink(
    url: str,
    content: str | None = None,
    *,
    supports_hyperlinks_override: bool | None = None,
) -> str:
    """Wrap *url* in an OSC 8 hyperlink escape sequence.

    When the terminal does **not** support hyperlinks the raw *url* is
    returned unchanged (no ANSI escapes, no OSC 8).

    Parameters
    ----------
    url:
        The URL to link to.  Must be a well-formed URI (e.g.
        ``https://example.com``, ``file:///path/to/file``).
    content:
        Visible link text.  Defaults to *url* when omitted.  The text is
        rendered in ANSI blue when hyperlinks are supported — otherwise it
        is ignored and the plain URL is returned.
    supports_hyperlinks_override:
        Force hyperlink support on / off.  When *None* (the default),
        ``supports_hyperlinks()`` is called automatically.
    """
    has_support = (
        supports_hyperlinks_override
        if supports_hyperlinks_override is not None
        else supports_hyperlinks()
    )
    return _maybe_hyperlink(url, content, has_support)


def create_hyperlink_with_id(
    url: str,
    content: str | None = None,
    *,
    link_id: str | None = None,
    supports_hyperlinks_override: bool | None = None,
) -> str:
    """Like ``create_hyperlink`` but allows an explicit OSC 8 *id*.

    An OSC 8 ``id`` groups multiple link spans so the terminal treats them as
    a single hyperlink (e.g. when the user right-clicks to copy).

    Parameters
    ----------
    url:
        The URL to link to.
    content:
        Visible link text.  Defaults to *url*.
    link_id:
        OSC 8 ``id`` parameter.  When omitted no ``id`` is emitted.
    supports_hyperlinks_override:
        Force hyperlink support on / off.
    """
    has_support = (
        supports_hyperlinks_override
        if supports_hyperlinks_override is not None
        else supports_hyperlinks()
    )
    if not has_support:
        return url

    display = content if content is not None else url
    coloured = f"{_ANSI_BLUE}{display}{_ANSI_RESET}"

    if link_id is not None:
        osc8_open = f"\x1b]8;id={link_id};{url}\x07"
    else:
        osc8_open = f"{OSC8_START}{url}{OSC8_END}"

    return f"{osc8_open}{coloured}{OSC8_START}{OSC8_END}"


def hyperlink(
    url: str,
    content: str | None = None,
    *,
    supports_hyperlinks_override: bool | None = None,
) -> str:
    """Convenience alias for ``create_hyperlink``.

    Provides a terser name for the common case::

        hyperlink("https://example.com", "docs")
    """
    return create_hyperlink(
        url, content, supports_hyperlinks_override=supports_hyperlinks_override,
    )


# ---------------------------------------------------------------------------
# File and mailto hyperlink builders
# ---------------------------------------------------------------------------


def create_file_hyperlink(
    file_path: str,
    content: str | None = None,
    *,
    line: int | None = None,
    column: int | None = None,
    supports_hyperlinks_override: bool | None = None,
) -> str:
    """Create an OSC 8 hyperlink pointing to a local file.

    Builds a ``file://`` URI from *file_path* and optionally appends line
    and column numbers.  The path is automatically expanded and absolutised.

        >>> create_file_hyperlink("/src/main.py", "main.py", line=42, column=5)

    Parameters
    ----------
    file_path:
        Filesystem path (relative or absolute).
    content:
        Visible link text.  Defaults to the basename with optional
        ``:line:column`` suffix.
    line:
        Optional 1-based line number.
    column:
        Optional 1-based column number.
    supports_hyperlinks_override:
        Force hyperlink support on / off.
    """
    abs_path = os.path.abspath(os.path.expanduser(file_path))
    url = "file://" + abs_path

    if line is not None:
        url += f"#L{line}"
        if column is not None:
            url += f":{column}"

    if content is None:
        filename = os.path.basename(abs_path)
        if line is not None:
            suffix = f":{line}"
            if column is not None:
                suffix += f":{column}"
            content = filename + suffix
        else:
            content = filename

    return create_hyperlink(
        url, content, supports_hyperlinks_override=supports_hyperlinks_override,
    )


# ---------------------------------------------------------------------------
# Utilities — stripping and detection
# ---------------------------------------------------------------------------


def strip_osc8(text: str) -> str:
    """Remove all OSC 8 escape sequences from *text*.

    Useful when piping output to a file or a terminal that does not support
    OSC 8.  Only the escape codes are stripped — the visible text is
    preserved.

        >>> strip_osc8("\\x1b]8;;https://x.com\\x07X\\x1b]8;;\\x07")
        'X'
    """
    return _OSC8_RE.sub("", text)


def strip_ansi(text: str) -> str:
    """Remove all ANSI escape sequences (including OSC 8) from *text*.

    Returns plain text suitable for log files, clipboard, etc.
    """
    ansi_re = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
    return _OSC8_RE.sub("", ansi_re.sub("", text))


def is_osc8(text: str) -> bool:
    """Return *True* when *text* contains at least one OSC 8 sequence."""
    return bool(_OSC8_RE.search(text))


def extract_osc8_urls(text: str) -> list[str]:
    """Return every URL wrapped in an OSC 8 sequence within *text*.

        >>> extract_osc8_urls("\\x1b]8;;https://a.com\\x07A\\x1b]8;;\\x07")
        ['https://a.com']
    """
    url_re = re.compile(r"\x1b\]8;;([^\x07]+)\x07")
    return url_re.findall(text)


# ---------------------------------------------------------------------------
# Utilities — structured parsing
# ---------------------------------------------------------------------------


def parse_osc8(text: str) -> list[Osc8Parsed]:
    """Parse every OSC 8 hyperlink in *text* into structured form.

    Each result carries the URL, visible text, optional link id, and byte
    offsets so callers can replace or transform individual links.

        >>> result = parse_osc8(
        ...     "\\x1b]8;;https://a.com\\x07click\\x1b]8;;\\x07"
        ... )
        >>> result[0].url
        'https://a.com'
        >>> result[0].text
        'click'
    """
    results: list[Osc8Parsed] = []
    for m in _OSC8_FULL_RE.finditer(text):
        results.append(Osc8Parsed(
            url=m.group("url"),
            text=m.group("text"),
            link_id=m.group("id") or None,
            start=m.start(),
            end=m.end(),
        ))
    return results


# ---------------------------------------------------------------------------
# Utilities — visible-length measurement
# ---------------------------------------------------------------------------


def visible_length(text: str) -> int:
    """Return the visual length of *text* after stripping OSC 8 and ANSI codes.

    Useful when calculating column widths in terminal output that contains
    hyperlinks.

        >>> visible_length("\\x1b]8;;https://x.com\\x07X\\x1b]8;;\\x07")
        1
    """
    return len(strip_ansi(text))


# ---------------------------------------------------------------------------
# HyperlinkBuilder — composable multi-link text
# ---------------------------------------------------------------------------


class HyperlinkBuilder:
    """Compose text interspersed with OSC 8 hyperlinks.

    Accumulates segments of plain text and hyperlinks, then renders the
    whole thing in a single pass.  Useful when constructing CLI output that
    mixes prose and clickable references.

    Usage::

        builder = HyperlinkBuilder()
        builder.add_text("See ")
        builder.add_link("https://example.com", "the docs")
        builder.add_text(" for details.")
        print(builder.build())
    """

    __slots__ = ("_segments",)

    def __init__(self) -> None:
        self._segments: list[tuple[str, str, str | None, str | None]] = []

    def add_text(self, text: str) -> HyperlinkBuilder:
        """Append a plain-text segment."""
        self._segments.append(("text", text, None, None))
        return self

    def add_link(
        self,
        url: str,
        content: str | None = None,
        *,
        link_id: str | None = None,
    ) -> HyperlinkBuilder:
        """Append a hyperlink segment.

        Parameters
        ----------
        url:
            The URL target.
        content:
            Visible link text.  Defaults to *url*.
        link_id:
            Optional OSC 8 id for link grouping.
        """
        self._segments.append(("link", url, content, link_id))
        return self

    def add_file_link(
        self,
        file_path: str,
        content: str | None = None,
        *,
        line: int | None = None,
        column: int | None = None,
    ) -> HyperlinkBuilder:
        """Append a ``file://`` hyperlink segment.  See ``create_file_hyperlink``."""
        abs_path = os.path.abspath(os.path.expanduser(file_path))
        url = "file://" + abs_path
        if line is not None:
            url += f"#L{line}"
            if column is not None:
                url += f":{column}"

        if content is None:
            filename = os.path.basename(abs_path)
            if line is not None:
                suffix = f":{line}"
                if column is not None:
                    suffix += f":{column}"
                content = filename + suffix
            else:
                content = filename

        self._segments.append(("link", url, content, None))
        return self

    def build(
        self,
        *,
        supports_hyperlinks_override: bool | None = None,
    ) -> str:
        """Render all accumulated segments into a single string.

        Parameters
        ----------
        supports_hyperlinks_override:
            Force hyperlink support on / off.
        """
        has_support = (
            supports_hyperlinks_override
            if supports_hyperlinks_override is not None
            else supports_hyperlinks()
        )
        parts: list[str] = []

        for kind, url, content, link_id in self._segments:
            if kind == "text":
                parts.append(url)  # url slot holds plain text for "text" kind
            elif kind == "link":
                if not has_support:
                    parts.append(url)
                elif link_id is not None:
                    parts.append(create_hyperlink_with_id(
                        url, content, link_id=link_id,
                        supports_hyperlinks_override=True,
                    ))
                else:
                    parts.append(create_hyperlink(
                        url, content, supports_hyperlinks_override=True,
                    ))

        return "".join(parts)

    def clear(self) -> None:
        """Remove all accumulated segments."""
        self._segments.clear()

    @property
    def visible_text(self) -> str:
        """Return the visible text content (no OSC 8 / ANSI escapes)."""
        parts: list[str] = []
        for kind, url, content, _link_id in self._segments:
            if kind == "text":
                parts.append(url)
            else:
                parts.append(content if content is not None else url)
        return "".join(parts)

    def __len__(self) -> int:
        return len(self._segments)

    def __bool__(self) -> bool:
        return bool(self._segments)

    def __repr__(self) -> str:
        return f"<HyperlinkBuilder segments={len(self._segments)}>"


# ---------------------------------------------------------------------------
# Cached support detection
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _cached_supports_hyperlinks() -> bool:
    """Cached version of ``supports_hyperlinks()``.

    Callers that check support repeatedly (e.g. inside a rendering loop) can
    use this to avoid repeated ``os.environ`` dictionary lookups.  The cache
    is keyed on no arguments, so it is valid for the lifetime of the process.
    """
    return supports_hyperlinks()


def invalidate_hyperlink_cache() -> None:
    """Clear the cached hyperlink-support result.

    Call this after changing environment variables that affect detection
    (e.g. ``TERM_PROGRAM``) if you need a fresh evaluation.
    """
    _cached_supports_hyperlinks.cache_clear()


# ---------------------------------------------------------------------------
# Smart link builder — auto-detect link type
# ---------------------------------------------------------------------------


def hyperlinkify(
    target: str,
    content: str | None = None,
    *,
    supports_hyperlinks_override: bool | None = None,
) -> str:
    """Create an OSC 8 hyperlink, auto-detecting the link type.

    Dispatches to the appropriate builder based on *target*:

    * ``https://...``, ``http://...`` — ``create_hyperlink``
    * ``mailto:...`` — ``create_mailto_hyperlink``
    * bare email address (contains ``@``, no scheme) — ``create_mailto_hyperlink``
    * anything else — treated as a filesystem path → ``create_file_hyperlink``

    This is the "do what I mean" entry point for CLI code that handles
    mixed user input.
    """
    if target.startswith(("http://", "https://")):
        return create_hyperlink(
            target, content,
            supports_hyperlinks_override=supports_hyperlinks_override,
        )
    if target.startswith("mailto:"):
        return create_mailto_hyperlink(
            target, content,
            supports_hyperlinks_override=supports_hyperlinks_override,
        )
    # Bare email address heuristic: has @, no ://, no whitespace
    if (
        "@" in target
        and "://" not in target
        and not re.search(r"\s", target)
        and target.count("@") == 1
    ):
        return create_mailto_hyperlink(
            f"mailto:{target}", content or target,
            supports_hyperlinks_override=supports_hyperlinks_override,
        )
    # Fallback: treat as file path
    return create_file_hyperlink(
        target, content,
        supports_hyperlinks_override=supports_hyperlinks_override,
    )


# ---------------------------------------------------------------------------
# Mailto hyperlink builder
# ---------------------------------------------------------------------------


def create_mailto_hyperlink(
    address: str,
    content: str | None = None,
    *,
    subject: str | None = None,
    body: str | None = None,
    cc: str | None = None,
    bcc: str | None = None,
    supports_hyperlinks_override: bool | None = None,
) -> str:
    """Create an OSC 8 ``mailto:`` hyperlink.

    Builds a ``mailto:`` URI with optional *subject*, *body*, *cc*, and *bcc*
    query parameters.  The visible link text defaults to the email address
    (stripped of the ``mailto:`` prefix if present).

    Parameters
    ----------
    address:
        Email address, optionally prefixed with ``mailto:``.
    content:
        Visible link text.  Defaults to the bare email address.
    subject:
        Optional ``subject`` query parameter.
    body:
        Optional ``body`` query parameter.
    cc:
        Optional ``cc`` query parameter.
    bcc:
        Optional ``bcc`` query parameter.
    supports_hyperlinks_override:
        Force hyperlink support on / off.
    """
    email = address.removeprefix("mailto:")
    url = f"mailto:{email}"

    params: list[str] = []
    if subject is not None:
        params.append(f"subject={quote(subject, safe='')}")
    if body is not None:
        params.append(f"body={quote(body, safe='')}")
    if cc is not None:
        params.append(f"cc={quote(cc, safe='')}")
    if bcc is not None:
        params.append(f"bcc={quote(bcc, safe='')}")
    if params:
        url += "?" + "&".join(params)

    display = content if content is not None else email
    return create_hyperlink(
        url, display, supports_hyperlinks_override=supports_hyperlinks_override,
    )


# ---------------------------------------------------------------------------
# Truncated hyperlink display
# ---------------------------------------------------------------------------


def create_truncated_hyperlink(
    url: str,
    max_display_length: int = 60,
    *,
    prefix_length: int | None = None,
    suffix_length: int | None = None,
    ellipsis: str = "…",
    supports_hyperlinks_override: bool | None = None,
) -> str:
    """Create an OSC 8 hyperlink whose visible text is a truncated form of the URL.

    Long URLs are abbreviated so they fit within a terminal column budget while
    remaining clickable.  The truncation preserves the prefix (scheme + host)
    and suffix (last path segment) with an ellipsis in between.

        >>> create_truncated_hyperlink("https://example.com/very/long/path/file.txt", 40)
        # Visible text: https://example.com/ver…/path/file.txt

    Parameters
    ----------
    url:
        The full URL target.
    max_display_length:
        Maximum length of the visible display text (including ellipsis).
    prefix_length:
        Characters to preserve from the start.  Defaults to roughly 60% of
        *max_display_length*.
    suffix_length:
        Characters to preserve from the end.  Defaults to roughly 40% of
        *max_display_length*.
    ellipsis:
        String to insert between prefix and suffix.
    supports_hyperlinks_override:
        Force hyperlink support on / off.
    """
    if len(url) <= max_display_length:
        return create_hyperlink(
            url, url, supports_hyperlinks_override=supports_hyperlinks_override,
        )

    if prefix_length is None:
        prefix_length = max(10, int(max_display_length * 0.55))
    if suffix_length is None:
        suffix_length = max(6, max_display_length - prefix_length - len(ellipsis))

    prefix_length = max(prefix_length, 6)
    suffix_length = max(suffix_length, 4)

    # Ensure we don't exceed the budget
    available = max_display_length - len(ellipsis)
    if prefix_length + suffix_length > available:
        prefix_length = max(6, available // 2)
        suffix_length = max(4, available - prefix_length)

    truncated = url[:prefix_length] + ellipsis + url[-suffix_length:]
    return create_hyperlink(
        url, truncated, supports_hyperlinks_override=supports_hyperlinks_override,
    )


# ---------------------------------------------------------------------------
# Proper file:// URL encoding
# ---------------------------------------------------------------------------


def encode_file_url(file_path: str) -> str:
    """Encode a filesystem path into a properly percent-encoded ``file://`` URI.

    Unlike naive string concatenation, this handles spaces, Unicode characters,
    and other special octets that would produce invalid URIs.

        >>> encode_file_url("/home/user/my file.txt")
        'file:///home/user/my%20file.txt'
    """
    abs_path = os.path.abspath(os.path.expanduser(file_path))

    # On Windows, convert backslashes to forward slashes
    if os.name == "nt":
        abs_path = abs_path.replace("\\", "/")

    encoded = quote(abs_path, safe="/:")
    return "file://" + encoded


# ---------------------------------------------------------------------------
# Plain-URL detection and auto-wrapping
# ---------------------------------------------------------------------------

# Matches raw http(s) URLs in text, avoiding URLs already inside OSC 8 sequences.
# Uses a negative lookbehind to skip URLs preceded by an OSC 8 start marker.
_PLAIN_URL_RE = re.compile(
    r"(?<!\x1b\]8;;)"
    r"https?://"
    r"[^\s\x1b\x07()<>\"'`\[\]{}]+",
)


def find_plain_urls(text: str) -> list[str]:
    """Return every raw ``http``/``https`` URL found in *text*.

    URLs that are already wrapped in OSC 8 sequences are excluded so you can
    safely call this on pre-formatted output.

        >>> find_plain_urls("see https://example.com and https://x.com")
        ['https://example.com', 'https://x.com']
    """
    return _PLAIN_URL_RE.findall(text)


def replace_plain_urls_with_links(
    text: str,
    *,
    supports_hyperlinks_override: bool | None = None,
) -> str:
    """Scan *text* for plain ``http``/``https`` URLs and wrap them in OSC 8 links.

    URLs that are already inside OSC 8 sequences are left untouched.  This is
    the primary "auto-linkify" utility for CLI output.

        >>> replace_plain_urls_with_links("docs at https://example.com")
        # Returns text with https://example.com wrapped in OSC 8 (if terminal supports it)

    Parameters
    ----------
    text:
        Arbitrary string that may contain bare URLs.
    supports_hyperlinks_override:
        Force hyperlink support on / off.  When *None*, auto-detects.
    """
    has_support = (
        supports_hyperlinks_override
        if supports_hyperlinks_override is not None
        else _cached_supports_hyperlinks()
    )
    if not has_support:
        return text

    # Build replacements from right-to-left to preserve offsets
    replacements: list[tuple[int, int, str]] = []
    for m in _PLAIN_URL_RE.finditer(text):
        url = m.group(0)
        # Strip trailing punctuation that is unlikely part of the URL
        stripped = url.rstrip(".,;:!?")
        if stripped != url:
            trailing = url[len(stripped):]
            replacements.append((
                m.start(),
                m.start() + len(stripped),
                _maybe_hyperlink(stripped, None, True),
            ))
            # Keep trailing punctuation as-is — it falls in the gap between replacements
        else:
            replacements.append((
                m.start(),
                m.end(),
                _maybe_hyperlink(url, None, True),
            ))

    if not replacements:
        return text

    # Apply replacements right-to-left
    result = text
    for start, end, replacement in reversed(replacements):
        result = result[:start] + replacement + result[end:]

    return result


