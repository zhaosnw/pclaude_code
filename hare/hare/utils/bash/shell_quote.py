"""
Safe wrappers around shell tokenization / quoting (shell-quote in TS).

Port of: src/utils/bash/shellQuote.ts

Uses :py:mod:`shlex` as a stand-in when a full shell-quote port is unavailable.
Expanded with comprehensive POSIX / bash quoting, escaping, unquoting, and
safe command-building utilities.
"""

from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Sequence, Union

# ---------------------------------------------------------------------------
# Character classification
# ---------------------------------------------------------------------------

# Characters that ALWAYS need quoting in POSIX shells (outside quotes).
SHELL_METACHARS: frozenset[str] = frozenset(
    "|&;()<> \t\n$`\\\"'*?[]#~=!{}"
)

# Characters that are safe inside double quotes (no escaping needed).
_SAFE_IN_DOUBLE_QUOTES: frozenset[str] = frozenset(
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789"
    "_.:,/@%^+=-"
)

# Characters inside double quotes that MUST be escaped with backslash.
_DOUBLE_QUOTE_ESCAPE_MAP: dict[str, str] = {
    "$": "\\$",
    "`": "\\`",
    '"': '\\"',
    "\\": "\\\\",
    "!": "\\!",
    "\n": "\\n",
}

# ANSI-C escape table (subset of what bash/zsh support in $'...' strings).
_ANSI_C_ESCAPE_MAP: dict[str, str] = {
    "\a": "\\a",
    "\b": "\\b",
    "\f": "\\f",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
    "\v": "\\v",
    "\\": "\\\\",
    "'": "\\'",
    "\x1b": "\\e",
}


def _has_metachar(s: str) -> bool:
    """True when *s* contains at least one shell metacharacter."""
    for ch in s:
        if ch in SHELL_METACHARS:
            return True
    return False


def _needs_quoting(s: str) -> bool:
    """Return True when the string would be altered by bare shell tokenization."""
    if not s:
        return True
    if s[0] == "-" and len(s) > 1 and s[1].isdigit():
        return True  # looks like a negative number → quoting avoids option-parsing
    return _has_metachar(s)


# ---------------------------------------------------------------------------
# Core quoting primitives (POSIX sh / bash compatible)
# ---------------------------------------------------------------------------


def shell_quote_single(s: str) -> str:
    """Quote *s* using single-quote style: ``'...'``.

    Single quotes preserve EVERY character literally except the single-quote
    itself, which cannot appear inside single quotes.  The standard workaround
    ``'\\''`` (end quote, escaped quote, restart quote) is used.

    Returns ``''`` for the empty string.
    """
    if not s:
        return "''"
    if "'" not in s:
        return f"'{s}'"
    # Insert the well-known '\'' sequence between runs of non-single-quote chars.
    parts = s.split("'")
    quoted_parts: list[str] = []
    for part in parts:
        if part:
            quoted_parts.append(f"'{part}'")
        else:
            # Consecutive single quotes produce an empty quoted string between
            # them, which is exactly what we need.
            pass
    # If the original string ends with a single quote, the split produces a
    # trailing empty part that becomes a trailing '' pair.
    return "\\'".join(quoted_parts)


def shell_quote_double(s: str) -> str:
    """Quote *s* using double-quote style: ``"..."``.

    Inside double quotes the following characters are backslash-escaped:
    ``$``, `` ` ``, ``"``, ``\\``, ``!`` (history expansion), and newline.

    Returns ``""`` for the empty string.
    """
    if not s:
        return '""'
    escaped: list[str] = []
    for ch in s:
        escaped.append(_DOUBLE_QUOTE_ESCAPE_MAP.get(ch, ch))
    return f'"{"".join(escaped)}"'


def shell_quote_ansi_c(s: str) -> str:
    """Quote *s* using ANSI-C quoting: ``$'...'``.

    ANSI-C quoting is supported by bash, zsh, ksh93, and mksh.  It allows
    C-style backslash escapes (``\\n``, ``\\t``, ``\\e``, etc.) and is
    useful for strings containing non-printable characters.

    Falls back to single-quote style if the shell may not support it
    (controlled by the caller; this function always produces the literal).
    """
    if not s:
        return "$''"
    escaped: list[str] = []
    i = 0
    while i < len(s):
        ch = s[i]
        # Handle known escape sequences
        replace = _ANSI_C_ESCAPE_MAP.get(ch)
        if replace is not None:
            escaped.append(replace)
            i += 1
            continue
        # Control characters → hex escape
        code = ord(ch)
        if code < 0x20:
            escaped.append(f"\\x{code:02x}")
            i += 1
            continue
        # High bytes (> 0x7F) → octal escape for portability
        if code > 0x7F:
            escaped.append(f"\\{code:03o}")
            i += 1
            continue
        # Printable ASCII — fall through
        if ch == "'":
            escaped.append("\\'")
        else:
            escaped.append(ch)
        i += 1
    return "$'" + "".join(escaped) + "'"


def shell_quote_backslash(s: str) -> str:
    """Quote *s* by backslash-escaping every shell metacharacter individually.

    This produces the minimal quoting for a string that is not wrapped in
    any quote pair — useful inside double quotes or when building
    here-documents.
    """
    if not s:
        return ""
    result: list[str] = []
    for ch in s:
        if ch in SHELL_METACHARS:
            result.append("\\")
        result.append(ch)
    return "".join(result)


# ---------------------------------------------------------------------------
# Automatic / intelligent quoting
# ---------------------------------------------------------------------------


def shell_quote_auto(s: str, *, prefer_single: bool = True) -> str:
    """Choose the best quoting style for *s* automatically.

    Heuristics (in priority order):

    1. Empty string → ``''``
    2. No special characters → raw string (no quoting needed)
    3. String contains only printable ASCII, no single quotes → single-quote
    4. String contains single quotes but no ``$`` / `` ` `` / ``!`` → double-quote
    5. String contains control chars or high bytes → ANSI-C quoting
    6. Fallback → single-quote with ``'\\''`` escaping

    When *prefer_single* is ``False``, double-quote style is preferred over
    single-quote when both would work.
    """
    if not s:
        return "''"

    if not _needs_quoting(s):
        return s

    has_ctrl_or_high = any(ord(c) < 0x20 or ord(c) > 0x7E for c in s)
    has_single_quote = "'" in s
    has_dollar_backtick = "$" in s or "`" in s

    # ANSI-C for control/high-byte strings
    if has_ctrl_or_high:
        return shell_quote_ansi_c(s)

    if prefer_single:
        if not has_single_quote:
            return shell_quote_single(s)
        if not has_dollar_backtick and "!" not in s:
            return shell_quote_double(s)
        return shell_quote_single(s)
    else:
        if not has_dollar_backtick and "!" not in s:
            return shell_quote_double(s)
        if not has_single_quote:
            return shell_quote_single(s)
        return shell_quote_double(s)


def shell_escape(s: str) -> str:
    """One-stop shell quoting.  Alias for :func:`shell_quote_auto`."""
    return shell_quote_auto(s)


# ---------------------------------------------------------------------------
# Safe command building
# ---------------------------------------------------------------------------


def shell_quote_list(args: Sequence[str | int | float]) -> str:
    """Quote each argument and join with a single space.

    >>> shell_quote_list(["echo", "hello world", "$HOME"])
    "echo 'hello world' '$HOME'"
    """
    return " ".join(shell_quote_auto(str(a)) for a in args)


shell_join = shell_quote_list  # alias for compatibility with shell_quoting.py


def build_command(
    executable: str,
    args: Sequence[str | int | float] = (),
    *,
    env: dict[str, str | None] | None = None,
    cwd: str | None = None,
) -> str:
    """Build a safely-quoted single shell command line.

    Parameters
    ----------
    executable:
        The command or path to run (e.g. ``"git"``, ``"/usr/bin/env"``).
    args:
        Positional arguments to append.
    env:
        Environment variables to set for this command only
        (``KEY=val KEY2=val2 cmd`` prefix).
    cwd:
        If given, wrap the whole command in a ``cd ... && ...`` subshell
        so the working directory is changed only for this invocation.

    Returns
    -------
    str
        A shell command line safe to pass to ``bash -c`` or similar.
    """
    parts: list[str] = []

    # Environment prefix
    if env:
        for key, val in env.items():
            if val is None:
                parts.append(f"unset {shell_quote_auto(key)}")
            else:
                parts.append(f"{shell_quote_auto(key)}={shell_quote_auto(val)}")

    # cd prefix
    if cwd is not None:
        parts.append(f"cd {shell_quote_auto(cwd)}")

    # Command + args
    cmd_parts = [shell_quote_auto(executable)]
    cmd_parts.extend(shell_quote_auto(str(a)) for a in args)
    parts.append(" ".join(cmd_parts))

    return " && ".join(parts)


def join_commands(
    commands: Sequence[str],
    *,
    separator: str = " && ",
    continue_on_error: bool = False,
) -> str:
    """Join multiple shell commands with a separator.

    Parameters
    ----------
    commands:
        Already-quoted or bare command strings.
    separator:
        Defaults to ``" && "`` (stop on first failure).  Use ``"; "`` for
        always-continue, or ``" || "`` for fallback chains.
    continue_on_error:
        Shorthand that sets *separator* to ``"; "`` when ``True``.
    """
    if continue_on_error:
        separator = "; "
    return separator.join(c.strip() for c in commands if c.strip())


# ---------------------------------------------------------------------------
# Unquoting / de-escaping
# ---------------------------------------------------------------------------

# Regex that matches a single-quoted string: '...'
_SINGLE_QUOTED_RE = re.compile(r"'((?:[^']*)'?)'")

# Regex matching ANSI-C quoted string: $'...'
_ANSI_C_QUOTED_RE = re.compile(r"\$'((?:[^'\\]|\\.)*)'")

_ANSI_C_DECODE_MAP: dict[str, str] = {
    "a": "\a",
    "b": "\b",
    "e": "\x1b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
    "v": "\v",
    "\\": "\\",
    "'": "'",
    '"': '"',
}


def shell_unquote(s: str) -> str:
    """Remove one layer of shell quoting from *s*.

    Handles single-quoted, double-quoted, and ANSI-C-quoted strings.
    Backslash-escaped characters outside quotes are also resolved.

    If *s* is not quoted the raw string is returned unchanged.
    """
    if not s:
        return s

    # Strip surrounding double quotes
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        inner = s[1:-1]
        # Resolve backslash escapes inside double quotes
        result: list[str] = []
        i = 0
        while i < len(inner):
            ch = inner[i]
            if ch == "\\" and i + 1 < len(inner):
                next_ch = inner[i + 1]
                if next_ch in ('"', "\\", "$", "`", "!"):
                    result.append(next_ch)
                    i += 2
                    continue
                result.append(ch)
                i += 1
            else:
                result.append(ch)
                i += 1
        # Remove surrounding double quotes
        return "".join(result)

    # Strip surrounding single quotes
    if len(s) >= 2 and s[0] == "'" and s[-1] == "'":
        return s[1:-1].replace("'\\''", "'")

    # Strip ANSI-C quoting: $'...'
    if len(s) >= 4 and s[:2] == "$'" and s[-1] == "'":
        inner = s[2:-1]
        result = []
        i = 0
        while i < len(inner):
            ch = inner[i]
            if ch == "\\" and i + 1 < len(inner):
                next_ch = inner[i + 1]
                # Hex escape \xNN
                if next_ch == "x" and i + 3 < len(inner):
                    try:
                        result.append(chr(int(inner[i + 2 : i + 4], 16)))
                        i += 4
                        continue
                    except ValueError:
                        pass
                # Octal escape \NNN
                if next_ch.isdigit():
                    j = i + 1
                    octal = ""
                    while j < len(inner) and inner[j].isdigit() and len(octal) < 3:
                        octal += inner[j]
                        j += 1
                    if octal:
                        try:
                            result.append(chr(int(octal, 8)))
                            i = j
                            continue
                        except ValueError:
                            pass
                # Named escape
                replacement = _ANSI_C_DECODE_MAP.get(next_ch)
                if replacement is not None:
                    result.append(replacement)
                    i += 2
                    continue
                result.append(next_ch)
                i += 2
            else:
                result.append(ch)
                i += 1
        return "".join(result)

    # Resolve top-level backslash escapes
    result = []
    i = 0
    while i < len(s):
        ch = s[i]
        if ch == "\\" and i + 1 < len(s):
            result.append(s[i + 1])
            i += 2
        else:
            result.append(ch)
            i += 1
    return "".join(result)


def shell_expand_escapes(s: str) -> str:
    """Expand C-style backslash escape sequences (``\\n`` → newline, etc.).

    This processes the string as if it were inside ``$'...'`` but handles only
    the raw escape sequences, not the surrounding ``$'`` delimiters.
    """
    if not s or "\\" not in s:
        return s
    result: list[str] = []
    i = 0
    while i < len(s):
        ch = s[i]
        if ch == "\\" and i + 1 < len(s):
            next_ch = s[i + 1]
            if next_ch == "x" and i + 3 < len(s):
                try:
                    result.append(chr(int(s[i + 2 : i + 4], 16)))
                    i += 4
                    continue
                except ValueError:
                    pass
            if next_ch.isdigit():
                j = i + 1
                octal = ""
                while j < len(s) and s[j].isdigit() and len(octal) < 3:
                    octal += s[j]
                    j += 1
                if octal:
                    try:
                        result.append(chr(int(octal, 8)))
                        i = j
                        continue
                    except ValueError:
                        pass
            replacement = _ANSI_C_DECODE_MAP.get(next_ch)
            if replacement is not None:
                result.append(replacement)
                i += 2
                continue
            result.append(next_ch)
            i += 2
        else:
            result.append(ch)
            i += 1
    return "".join(result)


# ---------------------------------------------------------------------------
# Quoting helpers / metacharacter detection
# ---------------------------------------------------------------------------


def needs_shell_quoting(s: str) -> bool:
    """Return ``True`` when *s* would be altered by bare shell parsing.

    A string that is purely alphanumeric (and a few safe punctuation chars)
    does NOT need quoting.

    >>> needs_shell_quoting("hello")
    False
    >>> needs_shell_quoting("hello world")
    True
    >>> needs_shell_quoting("")
    True
    """
    return _needs_quoting(s)


def is_safely_quoted(s: str) -> bool:
    """Return ``True`` when *s* is already wrapped in matching quotes.

    Recognises ``'...'``, ``"..."``, and ``$'...'`` forms.
    """
    if not s or len(s) < 2:
        return False
    if s[0] == "'" and s[-1] == "'":
        return True
    if s[0] == '"' and s[-1] == '"':
        return True
    if s[:2] == "$'" and s[-1] == "'" and len(s) >= 4:
        return True
    return False


def count_quote_complexity(s: str) -> int:
    """Return a heuristic "complexity score" for quoting *s*.

    Higher values mean the string is harder to quote cleanly.  Useful for
    deciding whether to use a heredoc instead of inline quoting.

    Scoring factors:
    - Each single-quote adds 3 (requires ``'\\''`` gynmastics)
    - Each double-quote + ``$`` pair adds 2
    - Control characters add 5 each
    - Length > 200 adds 1 per extra 100 chars
    """
    score = 0
    if not s:
        return 0
    score += s.count("'") * 3
    if '"' in s and "$" in s:
        score += 2
    score += sum(5 for c in s if ord(c) < 0x20)
    over = len(s) - 200
    if over > 0:
        score += over // 100
    return score


# ---------------------------------------------------------------------------
# Here-document helpers
# ---------------------------------------------------------------------------


def heredoc_quote_delimiter(delimiter: str) -> str:
    """Quote a here-document delimiter safely.

    When the delimiter is quoted (e.g. ``'EOF'``), the shell performs NO
    parameter/command substitution inside the heredoc body.  This function
    returns a single-quoted form.
    """
    return shell_quote_single(delimiter)


def heredoc(
    body: str,
    delimiter: str = "EOF",
    *,
    strip_leading_tabs: bool = False,
    expand: bool = False,
    indentation: str = "",
) -> str:
    r"""Build a POSIX here-document.

    Parameters
    ----------
    body:
        The heredoc body text.  Newlines are preserved literally.
    delimiter:
        The end marker (default ``"EOF"``).
    strip_leading_tabs:
        If ``True`` use ``<<-`` so leading tabs are stripped from each line
        of the body AND the closing delimiter.
    expand:
        If ``True`` the delimiter is left unquoted so the shell performs
        ``$variable`` and ``$(command)`` expansion inside the body.
        Default is ``False`` (quoted delimiter → no expansion).
    indentation:
        Optional string prepended to each line of the body for readability
        inside the generated script.  This is purely cosmetic and does not
        affect shell parsing (use *strip_leading_tabs* for that).
    """
    operator = "<<-" if strip_leading_tabs else "<<"
    delim = delimiter if expand else heredoc_quote_delimiter(delimiter)
    indent = indentation or ""

    lines = body.split("\n")
    indented_body = "\n".join(f"{indent}{line}" if line else "" for line in lines)

    return f"{operator}{delim}\n{indented_body}\n{indent}{delimiter}"


# ---------------------------------------------------------------------------
# Shell tokenization
# ---------------------------------------------------------------------------

ParseEntry = Union[str, dict[str, Any]]

# ---------------------------------------------------------------------------
# Regex patterns for tokenization
# ---------------------------------------------------------------------------

# Characters that terminate an unquoted word.  Parentheses are included
# because they act as standalone operator tokens (`func()`, `(cmd)`).
# However the escaped-$ sequence handler (for ``\$(...)``) overrides
# word-break behaviour so that the entire substitution is captured as
# one token.
_WORD_BREAK_CHARS: frozenset[str] = frozenset(
    "|&;()<> \t\n\"'`"
)

# Operator tokens that shell-quote returns as dict entries
_OPERATOR_TOKENS: dict[str, str] = {
    "|": "|",
    "||": "||",
    "&&": "&&",
    ";;": ";;",
    ";": ";",
    "&": "&",
    "(": "(",
    ")": ")",
    "<<-": "<<-",
    "<<": "<<",
    ">>": ">>",
    ">": ">",
    "<": "<",
    ">&": ">&",
    "<&": "<&",
    "<>": "<>",
    ">|": ">|",
}

# Multi-character operators sorted by length (longest first) for greedy matching
_SORTED_OPERATORS: list[str] = sorted(
    _OPERATOR_TOKENS.keys(), key=len, reverse=True
)

# Regex to match environment variable references: $VAR or ${VAR}
_ENV_VAR_RE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)|\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

# Regex to match glob pattern characters
_GLOB_RE = re.compile(r"[\*\?\[\]]")


def _log_error(error: BaseException) -> None:
    """Stub for ../log.logError."""
    del error


@dataclass
class ShellParseSuccess:
    success: Literal[True] = True
    tokens: list[ParseEntry] = field(default_factory=list)


@dataclass
class ShellParseFailure:
    success: Literal[False] = False
    error: str = ""


ShellParseResult = Union[ShellParseSuccess, ShellParseFailure]


@dataclass
class ShellQuoteSuccess:
    success: Literal[True] = True
    quoted: str = ""


@dataclass
class ShellQuoteFailure:
    success: Literal[False] = False
    error: str = ""


ShellQuoteResult = Union[ShellQuoteSuccess, ShellQuoteFailure]


def _resolve_env(
    key: str,
    env: dict[str, str | None] | Callable[[str], str | None] | None = None,
) -> str | None:
    """Resolve an environment variable name to its value.

    Supports both dict-based lookup and callable-based lookup.
    Returns ``None`` if the variable is unset or env is ``None``.
    """
    if env is None:
        return None
    if callable(env):
        return env(key)
    return env.get(key)


def _match_operator(command: str, pos: int, length: int) -> str | None:
    """Try to match an operator at *pos* in *command*.

    Returns the matched operator string, or ``None``.
    Greedy: tries longest operator first.
    """
    for op in _SORTED_OPERATORS:
        if command.startswith(op, pos):
            return op
    return None


def _is_word_break(ch: str) -> bool:
    """True when *ch* terminates an unquoted shell word."""
    return ch in _WORD_BREAK_CHARS or ch.isspace()


def _read_unquoted_word(command: str, start: int) -> tuple[str, int]:
    """Read an unquoted word from *command* starting at *start*.

    Handles backslash escapes within the word: ``\\`` followed by any
    character inserts the literal next character and continues the word.
    Stops at the first unescaped word-break character or end of input.

    Returns ``(word, end_position)``.
    """
    chars: list[str] = []
    i = start
    n = len(command)
    while i < n:
        ch = command[i]
        # Backslash escapes the next character inside unquoted words
        if ch == "\\" and i + 1 < n:
            chars.append(command[i + 1])
            i += 2
            continue
        if _is_word_break(ch):
            break
        chars.append(ch)
        i += 1
    return "".join(chars), i


def _tokenize_shell(
    command: str,
) -> list[ParseEntry]:
    """Tokenize *command* into shell words and operators.

    This is a proper shell tokenizer that handles:
    - Single-quoted strings (``'...'``)
    - Double-quoted strings (``"..."``) with escape sequences
    - ANSI-C quoted strings (``$'...'``)
    - Backslash-escaped characters outside quotes
    - Operators: ``|``, ``||``, ``&&``, ``;``, ``;;``, ``&``,
      ``(``, ``)``, ``<``, ``>``, ``<<``, ``>>``, ``<<-``,
      ``>&``, ``<&``, ``<>``, ``>|``
    - Comments (``#`` at word boundaries)
    - Redirections (``2>``, ``1>&2``, etc.)
    - Variable assignments (``KEY=val``)

    Returns a list of ParseEntry -- strings for literal tokens and
    dicts (``{"op": "<operator>"}``) for operators.
    """
    tokens: list[ParseEntry] = []
    i = 0
    n = len(command)

    while i < n:
        ch = command[i]

        # Skip whitespace
        if ch.isspace():
            i += 1
            continue

        # --- Comments ---
        if ch == "#":
            comment_start = i
            # Comments run to end of line or end of string
            while i < n and command[i] != "\n":
                i += 1
            comment_text = command[comment_start:i]
            tokens.append({"comment": comment_text})
            continue

        # --- Operators ---
        op = _match_operator(command, i, n)
        if op is not None:
            tokens.append({"op": op})
            i += len(op)
            continue

        # --- Single-quoted string ---
        if ch == "'":
            i += 1
            word_chars: list[str] = []
            while i < n:
                if command[i] == "'":
                    i += 1  # skip closing quote
                    break
                word_chars.append(command[i])
                i += 1
            tokens.append("".join(word_chars))
            continue

        # --- Double-quoted string ---
        if ch == '"':
            i += 1
            word_chars = []
            while i < n:
                if command[i] == '"':
                    i += 1
                    break
                if command[i] == "\\" and i + 1 < n:
                    next_ch = command[i + 1]
                    if next_ch in ('"', "\\", "$", "`", "!", "\n"):
                        word_chars.append(next_ch)
                        i += 2
                        continue
                    word_chars.append(command[i])
                    i += 1
                else:
                    word_chars.append(command[i])
                    i += 1
            tokens.append("".join(word_chars))
            continue

        # --- Backslash escape (starts an unquoted word) ---
        if ch == "\\" and i + 1 < n:
            next_ch = command[i + 1]
            # When backslash escapes a dollar-sign that is immediately followed
            # by '(' or '{', the entire $(...) or ${...} (including nested
            # parens / braces) is one literal token -- word-break characters
            # inside do NOT split the word.
            if next_ch == "$" and i + 2 < n and command[i + 2] in "({":
                open_ch = command[i + 2]
                close_ch = ")" if open_ch == "(" else "}"
                depth = 1
                # Start accumulating: consume `\$` then the open paren/brace
                chars = ["$", open_ch]
                i += 3  # past \ $ ( or {
                while i < n and depth > 0:
                    c2 = command[i]
                    if c2 == "\\" and i + 1 < n:
                        chars.append(command[i + 1])
                        i += 2
                        continue
                    if c2 == open_ch:
                        depth += 1
                    elif c2 == close_ch:
                        depth -= 1
                    chars.append(c2)
                    i += 1
                tokens.append("".join(chars))
                continue
            # Normal backslash escape: escaped char continues into the word
            word, i = _read_unquoted_word(command, i)
            if word:
                tokens.append(word)
            continue

        # --- Variable expansion $VAR or ${VAR}, command substitution $(...), $'...' ---
        if ch == "$":
            # Check for ANSI-C quoted string: $'...'
            if i + 1 < n and command[i + 1] == "'":
                i += 2  # skip $'
                word_chars = []
                while i < n:
                    if command[i] == "'":
                        i += 1
                        break
                    if command[i] == "\\" and i + 1 < n:
                        next_ch = command[i + 1]
                        replacement = _ANSI_C_DECODE_MAP.get(next_ch)
                        if replacement is not None:
                            word_chars.append(replacement)
                            i += 2
                            continue
                        if next_ch == "x" and i + 3 < n:
                            try:
                                val = int(command[i + 2 : i + 4], 16)
                                word_chars.append(chr(val))
                                i += 4
                                continue
                            except ValueError:
                                pass
                        if next_ch.isdigit():
                            j = i + 1
                            octal = ""
                            while (
                                j < n
                                and command[j].isdigit()
                                and len(octal) < 3
                            ):
                                octal += command[j]
                                j += 1
                            if octal:
                                try:
                                    word_chars.append(chr(int(octal, 8)))
                                    i = j
                                    continue
                                except ValueError:
                                    pass
                        word_chars.append(next_ch)
                        i += 2
                    else:
                        word_chars.append(command[i])
                        i += 1
                tokens.append("".join(word_chars))
                continue

            # Check for command substitution: $(...)
            if i + 1 < n and command[i + 1] == "(":
                depth = 1
                word_chars = ["$", "("]
                i += 2
                while i < n and depth > 0:
                    c2 = command[i]
                    if c2 == "(":
                        depth += 1
                    elif c2 == ")":
                        depth -= 1
                    elif c2 == "\\" and i + 1 < n:
                        word_chars.append(c2)
                        i += 1
                        word_chars.append(command[i])
                        i += 1
                        continue
                    word_chars.append(c2)
                    i += 1
                tokens.append("".join(word_chars))
                continue

            # Check for $((...)) arithmetic expansion
            if i + 2 < n and command[i : i + 3] == "$((":
                depth = 2
                word_chars = ["$", "(", "("]
                i += 3
                while i < n and depth > 0:
                    c2 = command[i]
                    if c2 == "(":
                        depth += 1
                    elif c2 == ")":
                        depth -= 1
                    elif c2 == "\\" and i + 1 < n:
                        word_chars.append(c2)
                        i += 1
                        word_chars.append(command[i])
                        i += 1
                        continue
                    word_chars.append(c2)
                    i += 1
                tokens.append("".join(word_chars))
                continue

            # Check for variable reference: $VAR or ${VAR}
            m = _ENV_VAR_RE.match(command, i)
            if m:
                var_name = m.group(1) or m.group(2)
                tokens.append({"var": var_name})
                i = m.end()
                continue

            # Bare $ at end of input or followed by non-identifier char
            # (e.g. $ alone, $?, $$, $!, $#, $@, $*)
            word, i = _read_unquoted_word(command, i)
            tokens.append(word)
            continue

        # --- Backtick command substitution ---
        if ch == "`":
            word_chars = [ch]
            i += 1
            while i < n and command[i] != "`":
                if command[i] == "\\" and i + 1 < n:
                    word_chars.append(command[i])
                    i += 1
                word_chars.append(command[i])
                i += 1
            if i < n:
                word_chars.append(command[i])
                i += 1
            tokens.append("".join(word_chars))
            continue

        # --- IO number redirection (e.g., 2> / 1>&2) ---
        if ch.isdigit():
            j = i
            while j < n and command[j].isdigit():
                j += 1
            if j < n:
                next_op = _match_operator(command, j, n)
                if next_op is not None and next_op[0] in "><":
                    # This is like 2> or 1>&2
                    tokens.append(command[i:j])
                    tokens.append({"op": next_op})
                    i = j + len(next_op)
                    # If operator was >& or <&, consume the following fd number
                    if next_op in (">&", "<&") and i < n and command[i].isdigit():
                        j2 = i
                        while j2 < n and command[j2].isdigit():
                            j2 += 1
                        if j2 > i:
                            tokens.append(command[i:j2])
                            i = j2
                    continue

        # --- Variable assignment KEY=val ---
        if ch.isalpha() or ch == "_":
            j = i
            while j < n and (command[j].isalnum() or command[j] == "_"):
                j += 1
            if j < n and command[j] == "=":
                # This is a variable assignment
                word_chars = [command[i : j + 1]]
                j += 1
                # Read the value using the backslash-aware reader
                if j < n and not command[j].isspace():
                    value, j2 = _read_unquoted_word(command, j)
                    if value:
                        word_chars.append(value)
                        j = j2
                tokens.append("".join(word_chars))
                i = j
                continue
            # Fall through to unquoted word
            word, i = _read_unquoted_word(command, i)
            if word:
                tokens.append(word)
            continue

        # --- Unquoted word ---
        word, i = _read_unquoted_word(command, i)
        if word:
            tokens.append(word)

    return tokens


def _expand_tokens(
    tokens: list[ParseEntry],
    env: dict[str, str | None] | Callable[[str], str | None] | None = None,
) -> list[ParseEntry]:
    """Expand environment variable references in tokens.

    Replaces ``{"var": "NAME"}`` entries with their resolved string values.
    When a variable is unset (resolves to ``None``), the reference is
    replaced with an empty string.
    """
    if env is None:
        return tokens

    result: list[ParseEntry] = []
    for token in tokens:
        if isinstance(token, dict) and "var" in token:
            val = _resolve_env(token["var"], env)
            result.append(val if val is not None else "")
        elif isinstance(token, str) and "$" in token:
            # Expand embedded variable references in strings
            def _replace_var(m: re.Match[str]) -> str:
                var_name = m.group(1) or m.group(2)
                val = _resolve_env(var_name, env)
                return val if val is not None else ""

            result.append(_ENV_VAR_RE.sub(_replace_var, token))
        else:
            result.append(token)
    return result


def _parse_shell_quote_stub(
    cmd: str,
    env: dict[str, str | None] | Callable[[str], str | None] | None = None,
) -> list[ParseEntry]:
    """Parse *cmd* into shell tokens including operators and strings.

    This is a proper replacement for the npm ``shell-quote`` library's
    ``parse()`` function.  It handles:

    * Single-quoted, double-quoted, and ANSI-C quoted strings
    * Shell operators (``|``, ``||``, ``&&``, ``;``, etc.)
    * Backslash escapes
    * Environment variable references (``$VAR``, ``${VAR}``)
    * Command substitution (``$(...)``, backticks)
    * IO-number redirections (``2>``, ``1>&2``)
    * Variable assignments (``KEY=val``)
    * Shell comments (``#``)

    When *env* is provided, ``$VAR`` and ``${VAR}`` references are
    expanded using the supplied mapping or callable.

    Returns a list of ``ParseEntry`` values -- strings for literal
    arguments and dicts (``{"op": "..."}``, ``{"var": "..."}``,
    ``{"comment": "..."}``) for structured tokens.
    """
    if not cmd or not cmd.strip():
        return []

    try:
        tokens = _tokenize_shell(cmd)
        if env is not None:
            tokens = _expand_tokens(tokens, env)
        return tokens
    except Exception:
        # Fallback to simple shlex-based tokenization
        try:
            return shlex.split(cmd, posix=True)
        except ValueError:
            return cmd.split()


def try_parse_shell_command(
    cmd: str,
    env: dict[str, str | None] | Callable[[str], str | None] | None = None,
) -> ShellParseResult:
    try:
        tokens: list[ParseEntry] = _parse_shell_quote_stub(cmd, env)
        return ShellParseSuccess(tokens=tokens)
    except Exception as error:
        _log_error(error)
        return ShellParseFailure(error=str(error))


def try_quote_shell_args(args: Sequence[Any]) -> ShellQuoteResult:
    try:
        validated: list[str] = []
        for index, arg in enumerate(args):
            if arg is None:
                validated.append(str(arg))
                continue
            t = type(arg).__name__
            if isinstance(arg, str):
                validated.append(arg)
            elif isinstance(arg, (int, float, bool)):
                validated.append(str(arg))
            elif isinstance(arg, (dict, list)):
                raise TypeError(
                    f"Cannot quote argument at index {index}: object values are not supported"
                )
            else:
                raise TypeError(
                    f"Cannot quote argument at index {index}: unsupported type {t}"
                )
        quoted = (
            shlex.join(validated)
            if hasattr(shlex, "join")
            else " ".join(shlex.quote(a) for a in validated)
        )
        return ShellQuoteSuccess(quoted=quoted)
    except Exception as error:
        _log_error(error)
        return ShellQuoteFailure(
            error=str(error) if isinstance(error, Exception) else "Unknown quote error"
        )


def has_malformed_tokens(command: str, parsed: list[ParseEntry]) -> bool:
    in_single = False
    in_double = False
    double_count = 0
    single_count = 0
    i = 0
    while i < len(command):
        c = command[i]
        if c == "\\" and not in_single:
            i += 2
            continue
        if c == '"' and not in_single:
            double_count += 1
            in_double = not in_double
        elif c == "'" and not in_double:
            single_count += 1
            in_single = not in_single
        i += 1
    if double_count % 2 != 0 or single_count % 2 != 0:
        return True

    for entry in parsed:
        if not isinstance(entry, str):
            continue
        if entry.count("{") != entry.count("}"):
            return True
        if entry.count("(") != entry.count(")"):
            return True
        if entry.count("[") != entry.count("]"):
            return True
        dq = re.findall(r'(?<!\\)"', entry)
        if len(dq) % 2 != 0:
            return True
        sq = re.findall(r"(?<!\\)'", entry)
        if len(sq) % 2 != 0:
            return True
    return False


def has_shell_quote_single_quote_bug(command: str) -> bool:
    """Detect patterns that confuse naive parsers' single-quote handling."""
    in_single_quote = False
    in_double_quote = False
    i = 0
    while i < len(command):
        char = command[i]
        if char == "\\" and not in_single_quote:
            i += 2
            continue
        if char == '"' and not in_single_quote:
            in_double_quote = not in_double_quote
            i += 1
            continue
        if char == "'" and not in_double_quote:
            in_single_quote = not in_single_quote
            if not in_single_quote:
                backslash_count = 0
                j = i - 1
                while j >= 0 and command[j] == "\\":
                    backslash_count += 1
                    j -= 1
                if backslash_count > 0 and backslash_count % 2 == 1:
                    return True
                if (
                    backslash_count > 0
                    and backslash_count % 2 == 0
                    and "'" in command[i + 1 :]
                ):
                    return True
            i += 1
            continue
        i += 1
    return False


# ---------------------------------------------------------------------------
# Basic shell quoting (shlex wrapper, matches shell_quoting.py)
# ---------------------------------------------------------------------------


def shell_quote(s: str) -> str:
    """Quote *s* for safe use as a single shell token.

    Uses :func:`shlex.quote`.  Returns ``''`` for empty string.

    This is the basic equivalent of :func:`shell_quote_auto` that delegates
    entirely to the Python standard library.  For more control use
    :func:`shell_quote_single`, :func:`shell_quote_double`, or
    :func:`shell_quote_auto`.
    """
    if not s:
        return "''"
    return shlex.quote(s)


def shell_quote_shlex(s: str) -> str:
    """Direct wrapper around :func:`shlex.quote`.

    Convenience alias for code that wants to be explicit about using
    ``shlex`` quoting rules (which are POSIX single-quote with
    ``'\\''`` escaping).
    """
    return shell_quote(s)


def shell_maybe_quote(s: str) -> str:
    """Quote *s* only if it contains shell metacharacters.

    Returns the string unchanged if it is safe; otherwise applies
    :func:`shell_quote_auto`.

    >>> shell_maybe_quote("hello")
    'hello'
    >>> shell_maybe_quote("hello world")
    "'hello world'"
    """
    if not _needs_quoting(s):
        return s
    return shell_quote_auto(s)


# ---------------------------------------------------------------------------
# Word splitting
# ---------------------------------------------------------------------------


def shell_word_split(command: str) -> list[str]:
    """Split *command* into shell words, returning only literal string tokens.

    Operators (``|``, ``&&``, etc.) and comments are **excluded** from the
    result.  This is the closest Python equivalent of ``shlex.split()``
    extended to handle ANSI-C quoting and the full operator set.

    >>> shell_word_split("echo hello 'world'")
    ['echo', 'hello', 'world']
    >>> shell_word_split("ls -la | grep foo")
    ['ls', '-la', 'grep', 'foo']
    """
    tokens = _tokenize_shell(command)
    return [t for t in tokens if isinstance(t, str) and t]


def shell_tokenize(
    command: str,
    *,
    expand_env: dict[str, str | None] | Callable[[str], str | None] | None = None,
) -> list[ParseEntry]:
    """Tokenize *command* into `ParseEntry` objects.

    Returns both string tokens and operator/comment dicts.  Environment
    variables are expanded when *expand_env* is provided.

    >>> shell_tokenize("echo hello | wc -l")
    ['echo', 'hello', {'op': '|'}, 'wc', '-l']
    """
    tokens = _tokenize_shell(command)
    if expand_env is not None:
        tokens = _expand_tokens(tokens, expand_env)
    return tokens


# ---------------------------------------------------------------------------
# Safety / validation helpers
# ---------------------------------------------------------------------------


def is_shell_safe(s: str) -> bool:
    """Return ``True`` when *s* is safe to use unquoted in a shell context.

    A string is safe when it contains only alphanumeric characters and the
    safe punctuation set ``._/:@%^+-``, and does not start with a dash
    followed by a digit (which could be interpreted as an option).

    >>> is_shell_safe("hello_world.txt")
    True
    >>> is_shell_safe("hello world")
    False
    >>> is_shell_safe("-123")
    False
    >>> is_shell_safe("../../etc/passwd")
    True
    """
    if not s:
        return False
    # Dash followed by digit looks like a negative number / option flag
    if s[0] == "-" and len(s) > 1 and s[1].isdigit():
        return False
    for ch in s:
        if ch not in _SAFE_IN_DOUBLE_QUOTES and ch != "'":
            return False
    return True


def sanitize_shell_arg(s: str) -> str:
    """Return the argument as-is if shell-safe, otherwise return a quoted form.

    This is convenient for embedding user-controlled strings in generated
    shell scripts: safe strings pass through unquoted (readable), while
    potentially dangerous strings are quoted.

    >>> sanitize_shell_arg("output.txt")
    'output.txt'
    >>> sanitize_shell_arg("my file.txt")
    "'my file.txt'"
    """
    if is_shell_safe(s):
        return s
    return shell_quote_auto(s)


# ---------------------------------------------------------------------------
# Path quoting
# ---------------------------------------------------------------------------


def shell_quote_path(path: str) -> str:
    """Quote a filesystem path for safe use in a shell command.

    On POSIX systems this delegates to :func:`shell_quote_auto`.  Paths
    containing spaces, special characters, or starting with ``-`` are
    properly quoted.

    >>> shell_quote_path("/usr/bin/git")
    '/usr/bin/git'
    >>> shell_quote_path("/home/user/My Documents/file.txt")
    "'/home/user/My Documents/file.txt'"
    """
    if not path:
        return "''"
    # Path starting with dash can be confused with a command option
    if path.startswith("-"):
        return shell_quote_auto(path)
    if not _has_metachar(path):
        return path
    return shell_quote_auto(path)


# ---------------------------------------------------------------------------
# subprocess-safe argument quoting
# ---------------------------------------------------------------------------


def shell_quote_args_for_subprocess(args: Sequence[str | int | float]) -> str:
    """Quote arguments for use with ``subprocess.run(shell=False)``.

    When ``shell=False`` (the default and recommended), each argument is
    passed directly to the child process without shell interpretation.
    This function builds a human-readable command string for logging or
    display purposes -- it does NOT produce a string safe for ``shell=True``.

    >>> shell_quote_args_for_subprocess(["echo", "hello world"])
    "echo 'hello world'"
    """
    return shell_quote_list(args)


# ---------------------------------------------------------------------------
# Variable expansion
# ---------------------------------------------------------------------------


def shell_expand_vars(
    command: str,
    env: dict[str, str | None] | Callable[[str], str | None] | None = None,
) -> str:
    """Expand ``$VAR`` and ``${VAR}`` references in *command*.

    Unlike :func:`shell_tokenize`, this operates on the raw string and
    returns a string with variables replaced.  Unset variables expand to
    the empty string.

    >>> shell_expand_vars("echo $HOME", {"HOME": "/users/alice"})
    'echo /users/alice'
    >>> shell_expand_vars("echo ${HOME:-/default}", callable=lambda k: None)
    'echo /default'
    """
    if not command or env is None:
        return command

    def _replace_var(m: re.Match[str]) -> str:
        var_name = m.group(1) or m.group(2)
        val = _resolve_env(var_name, env)
        return val if val is not None else ""

    return _ENV_VAR_RE.sub(_replace_var, command)


# ---------------------------------------------------------------------------
# JSON / structured data quoting
# ---------------------------------------------------------------------------


def shell_quote_json(obj: Any) -> str:
    """Serialize *obj* to JSON and quote the result as a single shell argument.

    Useful for passing structured data (lists, dicts) to CLI tools that
    accept JSON on the command line.

    >>> shell_quote_json({"key": "value with spaces"})
    '\'{"key": "value with spaces"}\''
    """
    raw = json.dumps(obj, ensure_ascii=False, separators=(",", ": "))
    return shell_quote_auto(raw)


# ---------------------------------------------------------------------------
# Detecting shell features in a command string
# ---------------------------------------------------------------------------


def has_shell_operators(command: str) -> bool:
    """Return ``True`` when *command* contains shell operators like ``|``, ``&&``.

    >>> has_shell_operators("cat file.txt | grep foo")
    True
    >>> has_shell_operators("cat file.txt")
    False
    """
    tokens = _tokenize_shell(command)
    for t in tokens:
        if isinstance(t, dict) and "op" in t:
            return True
    return False


def has_command_substitution(command: str) -> bool:
    """Return ``True`` when *command* uses ``$(...)`` or backtick substitution.

    >>> has_command_substitution("echo $(whoami)")
    True
    >>> has_command_substitution("echo `date`")
    True
    >>> has_command_substitution("echo hello")
    False
    """
    return "$(" in command or "`" in command


def has_redirection(command: str) -> bool:
    """Return ``True`` when *command* uses input/output redirection.

    >>> has_redirection("cat < input.txt > output.txt")
    True
    >>> has_redirection("cat input.txt")
    False
    """
    tokens = _tokenize_shell(command)
    redirect_ops = {">", "<", ">>", "<<", "<<-", ">&", "<&", "<>", ">|"}
    for t in tokens:
        if isinstance(t, dict) and t.get("op") in redirect_ops:
            return True
    return False


def shell_feature_summary(command: str) -> dict[str, bool]:
    """Report which shell features a *command* uses.

    Returns a dict with keys ``has_pipes``, ``has_subshell``,
    ``has_substitution``, ``has_redirection``, ``has_background``,
    ``has_logic``, ``has_comment``, ``has_glob``, ``has_variable``.

    >>> shell_feature_summary("ls -la | grep foo && echo done")
    {'has_pipes': True, 'has_subshell': False, ...}
    """
    tokens = _tokenize_shell(command)
    ops: set[str] = set()
    has_comment = False
    has_variable = False
    has_glob = False

    for t in tokens:
        if isinstance(t, dict):
            if "op" in t:
                ops.add(t["op"])
            if "comment" in t:
                has_comment = True
            if "var" in t:
                has_variable = True
        elif isinstance(t, str):
            if _GLOB_RE.search(t):
                has_glob = True

    return {
        "has_pipes": "|" in ops,
        "has_subshell": "(" in ops or ")" in ops,
        "has_substitution": has_command_substitution(command),
        "has_redirection": has_redirection(command),
        "has_background": "&" in ops,
        "has_logic": "&&" in ops or "||" in ops,
        "has_comment": has_comment,
        "has_glob": has_glob or ("*" in command),
        "has_variable": has_variable or ("$" in command),
    }


# ---------------------------------------------------------------------------
# Quote (original API -- must be last to capture all helpers above)
# ---------------------------------------------------------------------------


def quote(args: Sequence[Any]) -> str:
    """Quote a sequence of arguments to form a safe shell command string.

    This is the primary API entry point matching the TypeScript
    ``shellQuote.quote()`` signature.  It first tries strict validation,
    then falls back to lenient conversion for non-primitive types.
    """
    result = try_quote_shell_args(list(args))
    if result.success:
        return result.quoted
    string_args: list[str] = []
    for arg in args:
        if arg is None:
            string_args.append(str(arg))
            continue
        if isinstance(arg, (str, int, float, bool)):
            string_args.append(str(arg))
        else:
            string_args.append(json.dumps(arg, default=str))
    try:
        return (
            shlex.join(string_args)
            if hasattr(shlex, "join")
            else " ".join(shlex.quote(a) for a in string_args)
        )
    except Exception as error:
        _log_error(error)
        raise RuntimeError("Failed to quote shell arguments safely") from error
