"""
MagicDocs utility layer — document inspection primitives.

Low-level file-type detection via magic bytes, content classification,
encoding sniffing, content preview generation, and document statistics.
These primitives are consumed by `hare.services.magic_docs` for high-level
documentation generation.

Port of concepts from: libmagic, python-magic, charset-normalizer.
"""

from __future__ import annotations

import os
import re
import statistics
from typing import Any

# ---------------------------------------------------------------------------
# Magic byte signatures — map leading bytes to a human-readable file type.
# Each entry: (offset, bytes | tuple-of-tuples) -> label.
# ---------------------------------------------------------------------------

MAGIC_SIGNATURES: dict[str, tuple[int, bytes]] = {
    # Images
    "image/png": (0, b"\x89PNG\r\n\x1a\n"),
    "image/jpeg": (0, b"\xff\xd8\xff"),
    "image/gif": (0, b"GIF87a"),
    "image/gif-alt": (0, b"GIF89a"),
    "image/webp": (0, b"RIFF"),
    "image/bmp": (0, b"BM"),
    "image/tiff-le": (0, b"I I\x2a\x00"),
    "image/tiff-be": (0, b"MM\x00\x2a"),
    "image/ico": (0, b"\x00\x00\x01\x00"),
    # Compressed / archive
    "application/zip": (0, b"PK\x03\x04"),
    "application/x-gzip": (0, b"\x1f\x8b\x08"),
    "application/x-bzip2": (0, b"BZh"),
    "application/x-xz": (0, b"\xfd7zXZ\x00"),
    "application/zstd": (0, b"\x28\xb5\x2f\xfd"),
    "application/x-rar": (0, b"Rar!\x1a\x07"),
    "application/x-7z-compressed": (0, b"7z\xbc\xaf'\x1c"),
    # Documents
    "application/pdf": (0, b"%PDF-"),
    "application/msword": (0, b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"),
    "application/vnd.openxmlformats-officedocument": (0, b"PK\x03\x04"),
    "application/postscript": (0, b"%!PS"),
    # Executables
    "application/x-elf": (0, b"\x7fELF"),
    "application/x-mach-o-32": (0, b"\xfe\xed\xfa\xce"),
    "application/x-mach-o-64": (0, b"\xfe\xed\xfa\xcf"),
    "application/x-mach-o-fat": (0, b"\xca\xfe\xba\xbe"),
    "application/x-dosexec": (0, b"MZ"),
    # Audio / video
    "audio/mpeg": (0, b"\xff\xfb"),
    "audio/mpeg-alt": (0, b"ID3"),
    "audio/wav": (0, b"RIFF"),
    "audio/flac": (0, b"fLaC"),
    "audio/ogg": (0, b"OggS"),
    "video/mp4": (4, b"ftyp"),
    "video/webm": (0, b"\x1a\x45\xdf\xa3"),
    # Misc
    "application/sqlite3": (0, b"SQLite format 3\x00"),
    "font/ttf": (0, b"\x00\x01\x00\x00\x00"),
    "font/otf": (0, b"OTTO"),
    "font/woff": (0, b"wOFF"),
    "font/woff2": (0, b"wOF2"),
}

# Ordered list of encodings to try when sniffing text content.
# Prefer UTF-8; fall back through common encodings.
TEXT_ENCODINGS_ORDERED: list[str] = [
    "utf-8",
    "utf-16-le",
    "utf-16-be",
    "latin-1",
    "cp1252",
    "shift-jis",
]

# Common binary control character ranges / null bytes that signal
# non-text content even without a magic signature match.
_BINARY_THRESHOLD = 0.10  # 10% of non-printable bytes -> binary

# Regexes for content classification
_RE_MARKDOWN_HEADING = re.compile(r"^#{1,6}\s", re.MULTILINE)
_RE_MARKDOWN_LINK = re.compile(r"\[.+?\]\(.+?\)")
_RE_MARKDOWN_CODE_FENCE = re.compile(r"^```", re.MULTILINE)
_RE_MARKDOWN_LIST = re.compile(r"^[\-\*\+]\s", re.MULTILINE)
_RE_JSON = re.compile(r"^\s*[\{\[]", re.MULTILINE)
_RE_YAML = re.compile(r"^[\w\.\-]+:\s", re.MULTILINE)
_RE_XML = re.compile(r"<\?xml\s|<[a-zA-Z_]\w*[^>]*>")
_RE_HTML = re.compile(r"<html|<head|<body|<div|<span|<p[>\s]", re.IGNORECASE)
_RE_PYTHON = re.compile(r"^(import\s|from\s|def\s|class\s|@|\bprint\b)", re.MULTILINE)
_RE_JS = re.compile(r"^(import\s|export\s|const\s|let\s|var\s|function\s)", re.MULTILINE)
_RE_CSV = re.compile(r"^[^,\n]+(,[^,\n]+)+$", re.MULTILINE)
_RE_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

# ---------------------------------------------------------------------------
# File-type detection
# ---------------------------------------------------------------------------


def detect_file_type_from_bytes(data: bytes) -> str:
    """Detect file type from raw bytes using magic signatures.

    Args:
        data: Raw file content (first 8 KiB is sufficient).

    Returns:
        MIME-type string or "text/plain" if no signature matches.
        "application/zip" is returned for ZIP, docx, xlsx, jar, etc.
    """
    if not data:
        return "application/x-empty"

    for mime, (offset, sig) in MAGIC_SIGNATURES.items():
        if offset + len(sig) > len(data):
            continue
        if data[offset : offset + len(sig)] == sig:
            # Special-case: RIFF container — check sub-type.
            # If neither sub-type matches, fall through to the next sig entry
            # (another RIFF-based entry may handle it).
            if mime == "image/webp":
                if len(data) > 12 and data[8:12] == b"WEBP":
                    return "image/webp"
                continue
            if mime == "audio/wav":
                if len(data) > 12 and data[8:12] == b"WAVE":
                    return "audio/wav"
                continue
            # GIF has two possible signatures; treat both as image/gif
            if mime == "image/gif-alt":
                return "image/gif"
            # ZIP signature overlaps with Office OOXML — sniff further.
            # OOXML files contain "word/" or "xl/" in the first local file
            # header entry (filename field starts at offset 30).
            if mime == "application/zip":
                if (
                    len(data) > 38
                    and (b"word/" in data[30:] or b"word/" in data)
                ):
                    return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                if (
                    len(data) > 38
                    and (b"xl/" in data[30:] or b"xl/" in data)
                ):
                    return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                return "application/zip"
            return mime

    # No magic match — check if readable text
    if is_readable_text(data):
        return "text/plain"

    return "application/octet-stream"


def detect_file_type_from_path(path: str) -> str:
    """Detect file type by reading the file header and checking magic bytes.

    Args:
        path: Absolute or relative path to the file.

    Returns:
        MIME-type string.
    """
    if not os.path.isfile(path):
        return "application/x-not-found"

    file_size = os.path.getsize(path)
    if file_size == 0:
        return "application/x-empty"

    read_size = min(file_size, 8192)
    with open(path, "rb") as f:
        data = f.read(read_size)

    return detect_file_type_from_bytes(data)


# Backward-compatible alias
detect_file_type = detect_file_type_from_path


# ---------------------------------------------------------------------------
# Content classification
# ---------------------------------------------------------------------------


def classify_content(text: str) -> str:
    """Classify text content into a high-level document type.

    Heuristic-based; not exhaustive. Checks for the strongest signal first.

    Args:
        text: The text content to classify.

    Returns:
        One of: 'markdown', 'json', 'yaml', 'xml', 'html', 'python',
        'javascript', 'csv', 'text', 'empty'.
    """
    stripped = text.strip()
    if not stripped:
        return "empty"

    # YAML frontmatter signals markdown
    fm = _RE_FRONTMATTER.match(stripped)
    body = stripped[fm.end() :] if fm else stripped

    # Markdown: heading + multiple markers
    md_signals = 0
    if _RE_MARKDOWN_HEADING.search(body or stripped):
        md_signals += 1
    if _RE_MARKDOWN_LINK.search(body or stripped):
        md_signals += 1
    if _RE_MARKDOWN_CODE_FENCE.search(body or stripped):
        md_signals += 1
    if _RE_MARKDOWN_LIST.search(body or stripped):
        md_signals += 1
    if md_signals >= 2:
        return "markdown"

    # JSON: starts with { or [ and is valid JSON
    if _RE_JSON.match(stripped):
        import json

        try:
            json.loads(stripped)
            return "json"
        except (json.JSONDecodeError, ValueError):
            pass

    # XML declaration or tag-like
    if _RE_XML.match(stripped):
        if _RE_HTML.search(stripped):
            return "html"
        return "xml"

    if _RE_HTML.search(stripped):
        return "html"

    # YAML: key: value on first line(s) and not markdown
    yaml_lines = stripped.split("\n")
    yaml_signals = 0
    for line in yaml_lines[:10]:
        if _RE_YAML.match(line) and not _RE_MARKDOWN_HEADING.match(line):
            yaml_signals += 1
    if yaml_signals >= 3:
        return "yaml"

    # CSV: multiple lines with consistent comma-separated fields
    csv_lines = [l for l in stripped.split("\n")[:20] if l.strip()]
    if len(csv_lines) >= 2:
        field_counts = [len(l.split(",")) for l in csv_lines]
        if len(set(field_counts)) == 1 and field_counts[0] >= 2:
            return "csv"

    # Programming languages
    if _RE_PYTHON.search(stripped):
        return "python"
    if _RE_JS.search(stripped):
        return "javascript"

    # Fallback
    if md_signals >= 1:
        return "markdown"

    return "text"


# ---------------------------------------------------------------------------
# Encoding detection
# ---------------------------------------------------------------------------


def detect_encoding(data: bytes) -> str:
    """Detect the text encoding of raw bytes.

    Tries BOM detection first, then common encodings in priority order.
    If all decodings fail, returns "latin-1" (which never fails).

    Args:
        data: Raw bytes of the file content.

    Returns:
        Encoding name string (e.g. "utf-8", "cp1252", "latin-1").
    """
    if not data:
        return "utf-8"

    # BOM detection
    if data[:3] == b"\xef\xbb\xbf":
        return "utf-8"
    if data[:2] == b"\xff\xfe":
        return "utf-16-le"
    if data[:2] == b"\xfe\xff":
        return "utf-16-be"

    # Try each encoding in priority order
    for enc in TEXT_ENCODINGS_ORDERED:
        try:
            data.decode(enc)
            return enc
        except (UnicodeDecodeError, LookupError):
            continue

    return "latin-1"


def is_binary(data: bytes, sample_size: int = 8192) -> bool:
    """Determine whether raw bytes represent binary (non-text) content.

    Uses two heuristics:
    1. Null bytes in the first sample_size bytes signal binary.
    2. A high proportion of non-printable / control characters signals binary.

    Args:
        data: Raw bytes to inspect.
        sample_size: Maximum bytes to examine.

    Returns:
        True if the data appears to be binary content.
    """
    sample = data[:sample_size]
    if not sample:
        return False

    # Null byte check
    if b"\x00" in sample:
        return True

    # Control character proportion (excluding whitespace: \t \n \r)
    control_count = sum(
        1
        for b in sample
        if b < 0x20 and b not in (0x09, 0x0A, 0x0D)
    )
    if control_count / len(sample) > _BINARY_THRESHOLD:
        return True

    return False


def is_readable_text(data: bytes) -> bool:
    """Check whether raw bytes are readable text (not binary).

    Convenience wrapper around is_binary().

    Args:
        data: Raw bytes to inspect.

    Returns:
        True if the content appears to be readable text.
    """
    return not is_binary(data)


# ---------------------------------------------------------------------------
# Content extraction
# ---------------------------------------------------------------------------


def extract_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Extract YAML frontmatter from a document and return (metadata, body).

    Frontmatter is expected to be delimited by `---` at the very start of
    the document. The metadata is parsed as YAML if the yaml library is
    available; otherwise a minimal key-value parser is used.

    Args:
        text: The document text, possibly with frontmatter.

    Returns:
        Tuple of (metadata_dict, body_text). If no frontmatter is found,
        returns an empty dict and the original text.
    """
    stripped = text.strip()
    m = _RE_FRONTMATTER.match(stripped)
    if not m:
        return {}, text

    raw_front = m.group(1)
    body = stripped[m.end() :]

    metadata = _parse_simple_yaml(raw_front)
    return metadata, body


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    """Parse a simple flat YAML block without external dependencies.

    Handles string, integer, float, boolean, and null values.
    Does NOT handle nested structures, lists, or multiline strings.
    """
    result: dict[str, Any] = {}
    for line in text.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        if val == "" or val == "~":
            result[key] = None
        elif val.lower() in ("true", "yes", "on"):
            result[key] = True
        elif val.lower() in ("false", "no", "off"):
            result[key] = False
        else:
            try:
                if "." in val:
                    result[key] = float(val)
                else:
                    result[key] = int(val)
            except ValueError:
                # Strip surrounding quotes if present
                if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
                    val = val[1:-1]
                result[key] = val
    return result


def generate_content_preview(
    text: str,
    max_chars: int = 300,
    *,
    strip_frontmatter: bool = True,
) -> str:
    """Generate a human-readable content preview.

    Strips leading whitespace, optionally removes frontmatter, and truncates
    at a word boundary near max_chars. Adds an ellipsis if truncated.

    Args:
        text: The full document text.
        max_chars: Maximum characters in the preview.
        strip_frontmatter: If True, remove YAML frontmatter before previewing.

    Returns:
        A trimmed preview string.
    """
    if not text:
        return ""

    if strip_frontmatter:
        _, text = extract_frontmatter(text)

    stripped = text.strip()
    if len(stripped) <= max_chars:
        return stripped

    # Truncate at the last word boundary before max_chars
    truncated = stripped[:max_chars]
    # Find last space within the truncated portion
    last_space = max(
        truncated.rfind(" "),
        truncated.rfind("\n"),
        truncated.rfind("\t"),
    )
    if last_space > max_chars // 2:
        truncated = truncated[:last_space]

    return truncated.rstrip() + "..."


# ---------------------------------------------------------------------------
# Document statistics
# ---------------------------------------------------------------------------


def get_document_stats(text: str) -> dict[str, Any]:
    """Compute basic statistics about a document.

    Returns counts for lines, words, characters, paragraphs, headings,
    code blocks, and links. Useful for document analysis and summaries.

    Args:
        text: The document text.

    Returns:
        Dict with keys: lines, words, chars, chars_no_spaces, paragraphs,
        headings, code_blocks, links, images, avg_word_length,
        classification.
    """
    if not text:
        return {
            "lines": 0,
            "words": 0,
            "chars": 0,
            "chars_no_spaces": 0,
            "paragraphs": 0,
            "headings": 0,
            "code_blocks": 0,
            "links": 0,
            "images": 0,
            "avg_word_length": 0.0,
            "classification": "empty",
        }

    stripped = text.strip()
    lines = stripped.split("\n")

    # Word-level stats
    words = stripped.split()
    word_count = len(words)
    char_count = len(stripped)
    chars_no_spaces = sum(1 for c in stripped if not c.isspace())

    # Paragraphs (separated by blank lines)
    paragraphs = [p for p in re.split(r"\n\s*\n", stripped) if p.strip()]
    para_count = len(paragraphs)

    # Markdown features
    headings = len(_RE_MARKDOWN_HEADING.findall(stripped))
    code_blocks = len(_RE_MARKDOWN_CODE_FENCE.findall(stripped)) // 2
    links = len(_RE_MARKDOWN_LINK.findall(stripped))
    images = sum(
        1 for _ in re.finditer(r"!\[.*?\]\(.+?\)", stripped)
    )

    # Average word length
    word_lengths = [len(w) for w in words]
    avg_len = statistics.fmean(word_lengths) if word_lengths else 0.0

    return {
        "lines": len(lines),
        "words": word_count,
        "chars": char_count,
        "chars_no_spaces": chars_no_spaces,
        "paragraphs": para_count,
        "headings": headings,
        "code_blocks": code_blocks,
        "links": links,
        "images": images,
        "avg_word_length": round(avg_len, 1),
        "classification": classify_content(text),
    }


# ---------------------------------------------------------------------------
# Language detection (programming language identification from source code)
# ---------------------------------------------------------------------------

# Heuristic regex patterns for code language identification.
# Ordered roughly from most-distinctive to most-generic to avoid false matches.
_LANG_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("go", re.compile(
        r"^package\s+\w+\s*$", re.MULTILINE)),
    ("rust", re.compile(
        r"^\s*(fn\s+\w+\s*[<\(]|impl\s+\w+|pub\s+(fn|struct|enum|trait|mod)|use\s+\w+::|let\s+mut\s)",
        re.MULTILINE)),
    ("ruby", re.compile(
        r"^\s*(require\s+['\"]|gem\s+['\"]|module\s+\w+|class\s+\w+\s*<)",
        re.MULTILINE)),
    ("java", re.compile(
        r"^\s*(public\s+(class|interface|enum)\s|package\s+[\w.]+;)", re.MULTILINE)),
    ("kotlin", re.compile(
        r"^\s*(fun\s+\w+\s*\(|val\s+\w+\s*:|var\s+\w+\s*:)", re.MULTILINE)),
    ("swift", re.compile(
        r"^\s*(import\s+Foundation|import\s+UIKit|import\s+SwiftUI)", re.MULTILINE)),
    ("c", re.compile(
        r"^\s*#include\s*<", re.MULTILINE)),
    ("c++", re.compile(
        r"^\s*#include\s*<[^>]+>|^\s*template\s*<|std::|^\s*using\s+namespace",
        re.MULTILINE)),
    ("csharp", re.compile(
        r"^\s*(using\s+System|namespace\s+\w+)", re.MULTILINE)),
    ("typescript", re.compile(
        r"^\s*(interface\s+\w+\s*\{|type\s+\w+\s*=|:\s*(string|number|boolean|void)\b)",
        re.MULTILINE)),
    ("php", re.compile(
        r"<\?php", re.MULTILINE)),
    ("r", re.compile(
        r"^\s*(library\(|require\(|ggplot|dplyr::)", re.MULTILINE)),
    ("lua", re.compile(
        r"^\s*(local\s+\w+\s*=|function\s+\w+\s*\(.*\)\s*$)", re.MULTILINE)),
    ("perl", re.compile(
        r"^\s*(use\s+\w+(::\w+)*;|my\s+\$\w+)", re.MULTILINE)),
    ("haskell", re.compile(
        r"^\s*(module\s+\w+\s+where|import\s+qualified)", re.MULTILINE)),
    ("scala", re.compile(
        r"^\s*(object\s+\w+\s*\{|def\s+\w+\s*\[)", re.MULTILINE)),
    ("elixir", re.compile(
        r"^\s*(defmodule\s+\w+\s+do|def\s+\w+\s*\([^)]*\)\s+do|defp\s+\w+\s*\([^)]*\)\s+do)",
        re.MULTILINE)),
    ("clojure", re.compile(
        r"^\s*\((defn|def\s|ns\s|use\s|require\s)", re.MULTILINE)),
    ("dart", re.compile(
        r"^\s*(import\s+['\"]dart:|void\s+main\s*\(|Widget\s+build\s*\()",
        re.MULTILINE)),
    ("sql", re.compile(
        r"^\s*(SELECT|CREATE\s+TABLE|INSERT\s+INTO|ALTER\s+TABLE)\b", re.IGNORECASE | re.MULTILINE)),
    ("makefile", re.compile(
        r"^[a-zA-Z_][\w.\-]*\s*:\s*[^=]", re.MULTILINE)),
    ("dockerfile", re.compile(
        r"^\s*(FROM\s+|RUN\s+|CMD\s+|COPY\s+|ADD\s+|EXPOSE\s+)", re.MULTILINE)),
    ("toml", re.compile(
        r"^\s*\[[^\]]+\]\s*$", re.MULTILINE)),
    ("ini", re.compile(
        r"^\s*\[[^\]]+\]\s*$\n^[a-zA-Z_]+\s*=", re.MULTILINE)),
    ("diff", re.compile(
        r"^diff\s--git|^---\s|^\+\+\+\s|^@@\s", re.MULTILINE)),
    ("shell", re.compile(
        r"^\s*#!/bin/(ba)?sh|^\s*#!/usr/bin/env\s+(ba)?sh", re.MULTILINE)),
]


def detect_language(text: str, *, file_path: str = "") -> str:
    """Detect the programming language from source code text.

    Combines heuristic regex scanning with optional file-path extension
    hints. Falls back to the base `classify_content` result for known
    language families (python, javascript).

    Args:
        text: Source code text to analyze.
        file_path: Optional file path; used as a hint to break ties.

    Returns:
        Lowercase language name (e.g. "go", "rust", "ruby", "java")
        or the classify_content result if no code language matches.
    """
    if not text.strip():
        return "empty"

    # File-path extension hint takes priority when unambiguous
    ext_hint = _language_from_extension(file_path)
    if ext_hint and ext_hint not in ("python", "javascript", "json", "yaml", "xml"):
        if ext_hint in dict(_LANG_PATTERNS):
            return ext_hint

    # Heuristic regex scanning
    for lang, pattern in _LANG_PATTERNS:
        if pattern.search(text):
            return lang

    # Fall back to content classifier for python / javascript / etc.
    return classify_content(text)


def _language_from_extension(file_path: str) -> str:
    """Map a file extension to a language name.

    Args:
        file_path: File path from which to extract the extension.

    Returns:
        Language name or empty string if unrecognized.
    """
    if not file_path:
        return ""
    ext = os.path.splitext(file_path)[1].lower()
    mapping: dict[str, str] = {
        ".go": "go",
        ".rs": "rust",
        ".rb": "ruby",
        ".java": "java",
        ".kt": "kotlin",
        ".kts": "kotlin",
        ".swift": "swift",
        ".c": "c",
        ".h": "c",
        ".cpp": "c++",
        ".cc": "c++",
        ".cxx": "c++",
        ".hpp": "c++",
        ".cs": "csharp",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".php": "php",
        ".r": "r",
        ".R": "r",
        ".lua": "lua",
        ".pl": "perl",
        ".pm": "perl",
        ".hs": "haskell",
        ".scala": "scala",
        ".ex": "elixir",
        ".exs": "elixir",
        ".clj": "clojure",
        ".cljs": "clojure",
        ".edn": "clojure",
        ".dart": "dart",
        ".sql": "sql",
        ".sh": "shell",
        ".bash": "shell",
        ".zsh": "shell",
        ".fish": "shell",
    }
    return mapping.get(ext, "")


# ---------------------------------------------------------------------------
# Line ending detection and normalization
# ---------------------------------------------------------------------------

_LINE_ENDING_RE = re.compile(r"\r\n|\r|\n")


def detect_line_endings(text: str) -> str:
    """Detect the dominant line-ending style in a text.

    Args:
        text: The text to analyze.

    Returns:
        "CRLF" (Windows), "LF" (Unix), "CR" (old Mac), or "mixed".
        Returns "LF" for empty strings.
    """
    if not text:
        return "LF"

    crlf = text.count("\r\n")
    lf = text.count("\n") - crlf
    cr = text.count("\r") - crlf

    counts = {"CRLF": crlf, "LF": lf, "CR": cr}
    non_zero = {k: v for k, v in counts.items() if v > 0}

    if not non_zero:
        return "LF"
    if len(non_zero) == 1:
        return next(iter(non_zero))
    # Multiple styles present: return the dominant one
    dominant = max(non_zero, key=non_zero.get)  # type: ignore[arg-type]
    if max(non_zero.values()) * 0.5 > sum(non_zero.values()) - max(non_zero.values()):
        return dominant
    return "mixed"


def normalize_line_endings(text: str, style: str = "LF") -> str:
    """Normalize all line endings in text to a consistent style.

    Args:
        text: The text to normalize.
        style: Target style — "LF", "CRLF", or "CR".

    Returns:
        Text with all line endings converted to the requested style.
    """
    target = {"LF": "\n", "CRLF": "\r\n", "CR": "\r"}.get(style, "\n")
    # First normalize to LF, then convert to target
    normalized = _LINE_ENDING_RE.sub("\n", text)
    if target != "\n":
        normalized = normalized.replace("\n", target)
    return normalized


# ---------------------------------------------------------------------------
# Shebang / interpreter detection
# ---------------------------------------------------------------------------

_SHEBANG_RE = re.compile(r"^#!\s*(.*)$", re.MULTILINE)


def detect_shebang(text: str) -> str | None:
    """Extract the interpreter path from a shebang line.

    Handles both direct paths (``#!/usr/bin/python3``) and env invocations
    (``#!/usr/bin/env node``). Strips flags and trailing arguments.

    Args:
        text: The text to scan for a shebang line.

    Returns:
        Interpreter name/command (e.g. "python3", "node", "bash"),
        or None if no shebang is present.
    """
    if not text:
        return None
    m = _SHEBANG_RE.search(text)
    if not m:
        return None

    interpreter_line = m.group(1).strip()
    # Split into tokens, remove flags (tokens starting with -)
    tokens = interpreter_line.split()
    if not tokens:
        return None

    # Handle /usr/bin/env <command>
    if os.path.basename(tokens[0]) == "env" and len(tokens) > 1:
        # Skip any flags between env and the command
        for tok in tokens[1:]:
            if not tok.startswith("-"):
                return tok
        return tokens[1]

    # Direct interpreter path
    return os.path.basename(tokens[0])


# ---------------------------------------------------------------------------
# MIME type helpers: extension <-> MIME and category checks
# ---------------------------------------------------------------------------

# Extension (with leading dot) -> MIME type
_EXTENSION_TO_MIME: dict[str, str] = {
    # Text / code
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".rst": "text/x-rst",
    ".csv": "text/csv",
    ".tsv": "text/tab-separated-values",
    ".json": "application/json",
    ".yaml": "application/x-yaml",
    ".yml": "application/x-yaml",
    ".xml": "application/xml",
    ".html": "text/html",
    ".htm": "text/html",
    ".css": "text/css",
    ".js": "application/javascript",
    ".mjs": "application/javascript",
    ".cjs": "application/javascript",
    ".ts": "application/typescript",
    ".tsx": "application/typescript",
    ".jsx": "text/jsx",
    ".py": "text/x-python",
    ".pyi": "text/x-python",
    ".rb": "text/x-ruby",
    ".go": "text/x-go",
    ".rs": "text/x-rust",
    ".java": "text/x-java",
    ".kt": "text/x-kotlin",
    ".swift": "text/x-swift",
    ".c": "text/x-c",
    ".h": "text/x-c",
    ".cpp": "text/x-c++",
    ".hpp": "text/x-c++",
    ".cs": "text/x-csharp",
    ".php": "text/x-php",
    ".sql": "text/x-sql",
    ".sh": "text/x-shellscript",
    ".bash": "text/x-shellscript",
    ".zsh": "text/x-shellscript",
    ".toml": "application/toml",
    ".ini": "text/x-ini",
    ".cfg": "text/x-ini",
    ".conf": "text/x-ini",
    ".lock": "text/plain",
    ".dockerfile": "text/x-dockerfile",
    ".gitignore": "text/plain",
    ".env": "text/plain",
    ".log": "text/plain",
    ".diff": "text/x-diff",
    ".patch": "text/x-diff",
    # Images
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".tiff": "image/tiff",
    ".tif": "image/tiff",
    # Audio
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    # Video
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".avi": "video/x-msvideo",
    ".mov": "video/quicktime",
    ".mkv": "video/x-matroska",
    # Documents
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    # Archives
    ".zip": "application/zip",
    ".tar": "application/x-tar",
    ".gz": "application/gzip",
    ".bz2": "application/x-bzip2",
    ".xz": "application/x-xz",
    ".7z": "application/x-7z-compressed",
    ".rar": "application/vnd.rar",
    ".zst": "application/zstd",
    # Fonts
    ".ttf": "font/ttf",
    ".otf": "font/otf",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    # Binaries
    ".exe": "application/x-dosexec",
    ".dll": "application/x-dosexec",
    ".so": "application/x-sharedlib",
    ".dylib": "application/x-mach-o",
    ".wasm": "application/wasm",
    ".class": "application/java-vm",
}


def mime_from_extension(extension_or_path: str) -> str | None:
    """Resolve a MIME type from a file extension or path.

    Args:
        extension_or_path: A file extension (with or without leading dot,
            e.g. ".py" or "py") or a full file path.

    Returns:
        MIME type string or None if unrecognized.
    """
    ext = extension_or_path.lower()
    if not ext.startswith("."):
        ext = os.path.splitext("x" + ext)[1] or f".{ext}"
    else:
        # If it looks like a path, extract the extension
        if "/" in ext or "\\" in ext:
            ext = os.path.splitext(ext)[1]
    return _EXTENSION_TO_MIME.get(ext)


def extension_from_mime(mime_type: str) -> str | None:
    """Find the canonical file extension for a MIME type.

    Walks the extension-to-MIME mapping to find the first match.

    Args:
        mime_type: MIME type string (e.g. "image/png").

    Returns:
        Extension with leading dot (e.g. ".png") or None.
    """
    for ext, mime in _EXTENSION_TO_MIME.items():
        if mime == mime_type:
            return ext
    return None


def mime_category(mime_type: str) -> str:
    """Classify a MIME type into a high-level category.

    Args:
        mime_type: MIME type string.

    Returns:
        One of: "image", "audio", "video", "text", "document",
        "archive", "font", "binary", "unknown".
    """
    major = mime_type.split("/")[0] if "/" in mime_type else ""
    if major == "image":
        return "image"
    if major == "audio":
        return "audio"
    if major == "video":
        return "video"
    if major == "text":
        return "text"
    if major == "font":
        return "font"
    if major == "application":
        subtype = mime_type.split("/", 1)[1] if "/" in mime_type else ""
        if any(kw in subtype for kw in ("zip", "gzip", "bzip", "xz", "zstd",
                                         "tar", "rar", "7z", "compress")):
            return "archive"
        if any(kw in subtype for kw in ("pdf", "msword", "officedocument",
                                         "opendocument", "rtf", "postscript")):
            return "document"
        if any(kw in subtype for kw in ("json", "xml", "yaml", "toml",
                                         "javascript", "typescript")):
            return "text"
        if subtype in ("octet-stream",):
            return "binary"
        return "binary"
    return "unknown"


def is_text_mime(mime_type: str) -> bool:
    """Check whether a MIME type represents text content.

    Args:
        mime_type: MIME type string.

    Returns:
        True if the MIME type is text-based.
    """
    return mime_category(mime_type) in ("text",)


def is_image_mime(mime_type: str) -> bool:
    """Check whether a MIME type represents an image.

    Args:
        mime_type: MIME type string.

    Returns:
        True if the MIME type is image-based.
    """
    return mime_category(mime_type) == "image"


# ---------------------------------------------------------------------------
# Comment ratio analysis (code files)
# ---------------------------------------------------------------------------

# Per-language single-line comment prefix.  Falls back to "#" when unknown.
_COMMENT_PREFIXES: dict[str, str] = {
    "python": "#",
    "ruby": "#",
    "shell": "#",
    "bash": "#",
    "perl": "#",
    "r": "#",
    "yaml": "#",
    "toml": "#",
    "ini": "#",
    "go": "//",
    "rust": "//",
    "java": "//",
    "kotlin": "//",
    "swift": "//",
    "c": "//",
    "c++": "//",
    "csharp": "//",
    "javascript": "//",
    "typescript": "//",
    "php": "//",
    "dart": "//",
    "scala": "//",
    "sql": "--",
    "lua": "--",
    "haskell": "--",
    "elixir": "#",
    "clojure": ";",
}


def get_comment_ratio(text: str, *, language: str = "") -> dict[str, Any]:
    """Compute the comment-to-code ratio for a source file.

    Strips blank lines before counting. Handles single-line comments
    using the language-appropriate prefix. Multi-line /* */ comments are
    handled generically for C-family languages.

    Args:
        text: Source code text.
        language: Language name to determine comment syntax. Auto-detected
            from text if not provided.

    Returns:
        Dict with keys: total_lines, code_lines, comment_lines,
        blank_lines, comment_ratio (0.0-1.0), language.
    """
    if not text.strip():
        return {
            "total_lines": 0,
            "code_lines": 0,
            "comment_lines": 0,
            "blank_lines": 0,
            "comment_ratio": 0.0,
            "language": language or "empty",
        }

    if not language:
        language = detect_language(text)

    lines = text.split("\n")
    prefix = _COMMENT_PREFIXES.get(language, "#")
    prefix_len = len(prefix)

    total = len(lines)
    blank = 0
    code = 0
    comments = 0
    in_block_comment = False

    for line in lines:
        stripped = line.strip()
        if not stripped:
            blank += 1
            continue

        # Block comment tracking for C-family languages
        if language in ("c", "c++", "java", "javascript", "typescript",
                         "csharp", "go", "rust", "kotlin", "swift", "scala",
                         "dart", "php"):
            if in_block_comment:
                comments += 1
                if "*/" in stripped:
                    in_block_comment = False
                continue
            if stripped.startswith("/*"):
                comments += 1
                if "*/" not in stripped:
                    in_block_comment = True
                continue

        # HTML/XML-style comments
        if language in ("html", "xml"):
            if stripped.startswith("<!--"):
                comments += 1
                if "-->" not in stripped:
                    continue  # multi-line not tracked for block
                continue
            # Fall through to code

        # Single-line comment check
        if stripped.startswith(prefix):
            comments += 1
        else:
            code += 1

    comment_ratio = comments / max(total - blank, 1)
    return {
        "total_lines": total,
        "code_lines": code,
        "comment_lines": comments,
        "blank_lines": blank,
        "comment_ratio": round(comment_ratio, 3),
        "language": language,
    }
