"""Shell completion install/regenerate (`completionCache.ts`).

Features:
  - Detect the active shell (bash, zsh, fish)
  - Generate and cache completion scripts via the CLI binary
  - Install/uninstall completion sourcing lines in shell rc files
  - Regenerate completions after updates
  - Hyperlink formatting for terminals that support OSC 8
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import quote as url_quote

from hare.utils.debug import log_for_debugging
from hare.utils.errors import is_enoent as is_enoent_error
from hare.utils.log import log_error

# ── constants ────────────────────────────────────────────────────────────────

EOL = "\n"

# Per-shell source-line injection markers so uninstall can reliably find and
# remove the block without disturbing user-added content.
_BLOCK_START_MARKER = "# >>> hare shell completions >>>"
_BLOCK_END_MARKER = "# <<< hare shell completions <<<"

# Default timeout in seconds for the completion-generation subprocess.
_GENERATION_TIMEOUT = 120

# Shell names we recognise (used by `detect_shell`).
_SUPPORTED_SHELLS = frozenset({"zsh", "bash", "fish"})


# ── data types ───────────────────────────────────────────────────────────────


@dataclass
class ShellInfo:
    """Metadata for a supported interactive shell."""

    name: str
    rc_file: str
    cache_file: str
    completion_line: str
    shell_flag: str


@dataclass
class CompletionResult:
    """Outcome of a completion setup / uninstall operation."""

    success: bool
    message: str
    shell: ShellInfo | None = None


# ── helpers ──────────────────────────────────────────────────────────────────


def _supports_hyperlinks() -> bool:
    """Return True when the terminal is believed to support OSC-8 hyperlinks."""
    # Many modern terminals set one or more of these variables.
    term = os.environ.get("TERM", "")
    term_program = os.environ.get("TERM_PROGRAM", "").lower()
    # VTE-based, Windows Terminal, iTerm2, Kitty, WezTerm, foot, contour, Rio, etc.
    if term_program in {
        "vscode",
        "warp",
        "hyper",
        "iterm.app",
        "terminology",
        "konsole",
        "rio",
    }:
        return True
    if any(
        token in term
        for token in (
            "xterm-kitty",
            "wezterm",
            "foot",
            "contour",
            "tmux",
            "screen",
            "alacritty",
        )
    ):
        return True
    # VTE_VERSION env indicates a VTE-based terminal (gnome-terminal, tilix, …)
    if os.environ.get("VTE_VERSION"):
        return True
    # Windows Terminal sets WT_SESSION
    if os.environ.get("WT_SESSION"):
        return True
    return False


def _format_path_link(file_path: str) -> str:
    """Format a filesystem path as a clickable terminal hyperlink if supported.

    Returns a plain string when the terminal does not advertise hyperlink
    capability, mimicking the TypeScript `formatPathLink` behaviour.
    """
    if not _supports_hyperlinks():
        return file_path
    abs_path = os.path.abspath(file_path)
    file_url = "file://" + url_quote(abs_path, safe="/@")
    # OSC 8 hyperlink escape sequence:  ESC ] 8 ;; <url> ST  <text>  ESC ] 8 ;; ST
    return f"\x1b]8;;{file_url}\x07{file_path}\x1b]8;;\x07"


def sys_argv1() -> str:
    """Return the first CLI argument (the binary name / path), or ``"hare"``.

    Used to invoke the bundled ``completion`` subcommand of the running binary.
    """
    import sys

    return sys.argv[1] if len(sys.argv) > 1 else "hare"


def _exec_completion_no_throw(
    binary: str, shell_flag: str, cache_file: str
) -> subprocess.CompletedProcess | None:
    """Run the completion-generation subprocess and return its outcome.

    Mirrors the ``execFileNoThrow`` pattern: returns the CompletedProcess on
    success *or* failure (we inspect ``.returncode``), and returns ``None``
    only when the process cannot be launched at all (e.g. FileNotFoundError,
    TimeoutExpired, or other OSError).
    """
    try:
        return subprocess.run(
            [binary, "completion", shell_flag, "--output", cache_file],
            capture_output=True,
            timeout=_GENERATION_TIMEOUT,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        log_for_debugging(
            f"completion subprocess failed for {shell_flag}: {exc}"
        )
        return None


def _read_rc_file(rc_file: str) -> tuple[str, bool, OSError | None]:
    """Read the rc file contents.

    Returns ``(contents, existed_before, error)``.  When the file does *not*
    exist an empty string is returned with ``existed_before=False`` and
    ``error=None`` (ENOENT is *not* treated as an error here).
    """
    try:
        return Path(rc_file).read_text(encoding="utf-8"), True, None
    except OSError as e:
        if is_enoent_error(e):
            return "", False, None
        return "", False, e


def _write_rc_file(rc_file: str, content: str) -> OSError | None:
    """Atomically write *content* to *rc_file*, creating parent dirs.

    Returns ``None`` on success or the caught ``OSError``.
    """
    try:
        os.makedirs(os.path.dirname(rc_file), exist_ok=True)
        Path(rc_file).write_text(content, encoding="utf-8")
        return None
    except OSError as e:
        return e


def _remove_completion_block(rc_contents: str) -> tuple[str, int]:
    """Strip the hare-managed completion block from *rc_contents*.

    Returns ``(cleaned_contents, lines_removed)``.
    """
    lines = rc_contents.splitlines(keepends=True)
    new_lines: list[str] = []
    in_block = False
    removed = 0
    for line in lines:
        stripped = line.strip()
        if stripped == _BLOCK_START_MARKER:
            in_block = True
            removed += 1
            continue
        if stripped == _BLOCK_END_MARKER:
            in_block = False
            removed += 1
            continue
        if in_block:
            removed += 1
            continue
        new_lines.append(line)
    return "".join(new_lines), removed


def _inject_completion_block(rc_contents: str, completion_line: str) -> str:
    """Append (or replace) a clearly-delimited hare completion block."""
    cleaned, _ = _remove_completion_block(rc_contents)
    body = cleaned.rstrip("\n")
    sep = "\n" if body else ""
    block = (
        f"{sep}\n"
        f"{_BLOCK_START_MARKER}\n"
        f"{completion_line}\n"
        f"{_BLOCK_END_MARKER}\n"
    )
    return body + block


# ── shell detection ──────────────────────────────────────────────────────────


def detect_shell() -> ShellInfo | None:
    """Detect the current interactive shell from the ``SHELL`` env var.

    Returns a populated ``ShellInfo`` for zsh, bash, or fish; ``None``
    otherwise.
    """
    shell = os.environ.get("SHELL", "")
    home = str(Path.home())
    hare_dir = os.path.join(home, ".hare")

    if shell.endswith("/zsh") or shell.endswith("zsh.exe"):
        cache = os.path.join(hare_dir, "completion.zsh")
        return ShellInfo(
            name="zsh",
            rc_file=os.path.join(home, ".zshrc"),
            cache_file=cache,
            completion_line=f'[[ -f "{cache}" ]] && source "{cache}"',
            shell_flag="zsh",
        )
    if shell.endswith("/bash") or shell.endswith("bash.exe"):
        cache = os.path.join(hare_dir, "completion.bash")
        return ShellInfo(
            name="bash",
            rc_file=os.path.join(home, ".bashrc"),
            cache_file=cache,
            completion_line=f'[ -f "{cache}" ] && source "{cache}"',
            shell_flag="bash",
        )
    if shell.endswith("/fish") or shell.endswith("fish.exe"):
        xdg = os.environ.get("XDG_CONFIG_HOME") or os.path.join(home, ".config")
        cache = os.path.join(hare_dir, "completion.fish")
        return ShellInfo(
            name="fish",
            rc_file=os.path.join(xdg, "fish", "config.fish"),
            cache_file=cache,
            completion_line=f'[ -f "{cache}" ] && source "{cache}"',
            shell_flag="fish",
        )
    return None


def get_cache_file_path() -> str | None:
    """Return the expected completion cache file path for the detected shell.

    Convenience helper so callers don't need to reach into ``ShellInfo``.
    """
    sh = detect_shell()
    return sh.cache_file if sh else None


# ── public API ───────────────────────────────────────────────────────────────


async def setup_shell_completion(theme: str = "") -> str:
    """Generate and cache the completion script, then add a source line to the
    shell's rc file.  Returns a user-facing status message.

    *theme* is accepted for API parity with the TypeScript version; colouring
    is not yet implemented.
    """
    sh = detect_shell()
    if not sh:
        return ""

    # Ensure the cache directory exists ---------------------------------------
    try:
        os.makedirs(os.path.dirname(sh.cache_file), exist_ok=True)
    except OSError as e:
        log_error(e)
        return (
            f"{EOL}"
            f"Could not write {sh.name} completion cache{EOL}"
            f"Run manually: hare completion {sh.shell_flag} > {sh.cache_file}{EOL}"
        )

    # Generate the completion script ------------------------------------------
    claude_bin = sys_argv1()
    result = _exec_completion_no_throw(claude_bin, sh.shell_flag, sh.cache_file)
    if result is None or result.returncode != 0:
        stderr_hint = ""
        if result and result.stderr:
            stderr_hint = f"{result.stderr.decode('utf-8', errors='replace').strip()[:200]}"
        log_for_debugging(
            f"completion generation failed for {sh.name}: "
            f"returncode={result.returncode if result else 'N/A'} "
            f"stderr={stderr_hint}"
        )
        return (
            f"{EOL}"
            f"Could not generate {sh.name} shell completions{EOL}"
            f"Run manually: hare completion {sh.shell_flag} > {sh.cache_file}{EOL}"
        )

    # Check if rc file already sources completions (by our marker or content) -
    existing, existed_before, read_err = _read_rc_file(sh.rc_file)
    if read_err is not None:
        log_error(read_err)
        return (
            f"{EOL}"
            f"Could not install {sh.name} shell completions{EOL}"
            f"Add to {_format_path_link(sh.rc_file)}:{EOL}"
            f"{sh.completion_line}{EOL}"
        )

    already_installed = (
        _BLOCK_START_MARKER in existing
        or "hare completion" in existing
        or sh.cache_file in existing
    )
    if already_installed:
        return (
            f"{EOL}"
            f"Shell completions updated for {sh.name}{EOL}"
            f"See {_format_path_link(sh.rc_file)}{EOL}"
        )

    # Append source line to rc file inside a delimited block ------------------
    new_contents = _inject_completion_block(existing, sh.completion_line)
    write_err = _write_rc_file(sh.rc_file, new_contents)
    if write_err is not None:
        log_error(write_err)
        return (
            f"{EOL}"
            f"Could not install {sh.name} shell completions{EOL}"
            f"Add to {_format_path_link(sh.rc_file)}:{EOL}"
            f"{sh.completion_line}{EOL}"
        )

    return (
        f"{EOL}"
        f"Installed {sh.name} shell completions{EOL}"
        f"Added to {_format_path_link(sh.rc_file)}{EOL}"
        f"Run: source {sh.rc_file}{EOL}"
    )


async def uninstall_shell_completion() -> CompletionResult:
    """Remove the hare completion block from the shell rc file.

    Does **not** delete the cached completion script itself — only the
    sourcing line in the rc file.
    """
    sh = detect_shell()
    if not sh:
        return CompletionResult(
            success=False,
            message="Could not detect shell; nothing to uninstall.",
            shell=None,
        )

    existing, existed_before, read_err = _read_rc_file(sh.rc_file)
    if read_err is not None:
        log_error(read_err)
        return CompletionResult(
            success=False,
            message=(
                f"Could not read {_format_path_link(sh.rc_file)}: {read_err}"
            ),
            shell=sh,
        )

    if not existed_before:
        return CompletionResult(
            success=True,
            message=f"No rc file found at {_format_path_link(sh.rc_file)}; nothing to do.",
            shell=sh,
        )

    cleaned, removed = _remove_completion_block(existing)
    if removed == 0:
        return CompletionResult(
            success=True,
            message=f"No hare completion block found in {_format_path_link(sh.rc_file)}.",
            shell=sh,
        )

    write_err = _write_rc_file(sh.rc_file, cleaned)
    if write_err is not None:
        log_error(write_err)
        return CompletionResult(
            success=False,
            message=(
                f"Could not update {_format_path_link(sh.rc_file)}: {write_err}{EOL}"
                f"You can manually remove the block between "
                f"'{_BLOCK_START_MARKER}' and '{_BLOCK_END_MARKER}'."
            ),
            shell=sh,
        )

    return CompletionResult(
        success=True,
        message=(
            f"Removed hare shell completions from {_format_path_link(sh.rc_file)}.{EOL}"
            f"Run 'source {sh.rc_file}' or restart your shell for the change to take effect."
        ),
        shell=sh,
    )


def is_completion_installed() -> bool:
    """Return ``True`` when the rc file already contains a hare completion block
    or a reference to the hare cache file.
    """
    sh = detect_shell()
    if not sh:
        return False
    existing, _, read_err = _read_rc_file(sh.rc_file)
    if read_err is not None:
        return False
    return (
        _BLOCK_START_MARKER in existing
        or _BLOCK_END_MARKER in existing
        or "hare completion" in existing
        or sh.cache_file in existing
    )


async def regenerate_completion_cache() -> None:
    """Regenerate cached shell completion scripts in ``~/.hare/``.

    Called after ``hare update`` so completions stay in sync with the new
    binary.  Failures are logged for debugging but never raised — a stale
    completion file is harmless.
    """
    sh = detect_shell()
    if not sh:
        return

    log_for_debugging(f"update: Regenerating {sh.name} completion cache")

    claude_bin = sys_argv1()
    result = _exec_completion_no_throw(claude_bin, sh.shell_flag, sh.cache_file)

    if result is None or result.returncode != 0:
        stderr_hint = ""
        if result and result.stderr:
            stderr_hint = (
                result.stderr.decode("utf-8", errors="replace").strip()[:200]
            )
        log_for_debugging(
            f"update: Failed to regenerate {sh.name} completion cache"
            + (f" — {stderr_hint}" if stderr_hint else "")
        )
        return

    log_for_debugging(
        f"update: Regenerated {sh.name} completion cache at {sh.cache_file}"
    )


async def refresh_completion_for_current_shell() -> CompletionResult:
    """One-stop convenience: detect the shell, regenerate the cache, and
    ensure the rc file is wired up.

    Returns a ``CompletionResult`` describing the outcome.
    """
    sh = detect_shell()
    if not sh:
        return CompletionResult(
            success=False,
            message="Could not detect a supported shell (bash/zsh/fish).",
        )

    await regenerate_completion_cache()

    installed = is_completion_installed()
    if installed:
        return CompletionResult(
            success=True,
            message=(
                f"{sh.name} shell completions are installed.{EOL}"
                f"Cache: {_format_path_link(sh.cache_file)}{EOL}"
                f"RC file: {_format_path_link(sh.rc_file)}"
            ),
            shell=sh,
        )
    else:
        return CompletionResult(
            success=False,
            message=(
                f"{sh.name} completion cache was regenerated but the rc file "
                f"is not wired up.{EOL}"
                f"Run 'hare completion install' to enable completions."
            ),
            shell=sh,
        )


def supported_shells() -> frozenset:
    """Return the set of shell names that ``detect_shell`` recognises."""
    return _SUPPORTED_SHELLS
