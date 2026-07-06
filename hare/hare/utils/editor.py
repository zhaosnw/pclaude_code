"""
Launch the user's external editor for a file, with robust editor detection
and cross-platform launching support.

Port of: src/utils/editor.ts

Expanded with:
  - Comprehensive editor detection (env vars, git config, IDE hints, PATH)
  - Editor family classification with capability flags
  - Rich launch primitives for files, diffs, and directories
  - Cross-platform support (macOS, Linux, Windows, WSL, VS Code remote)
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from enum import Enum, auto
from functools import lru_cache
from pathlib import Path

from hare.utils.debug import log_for_debugging
from hare.utils.which import which_sync

# ---------------------------------------------------------------------------
# Editor families
# ---------------------------------------------------------------------------


class EditorFamily(Enum):
    """Broad editor category used to select launching strategy."""

    VSCODE = auto()  # code / cursor / windsurf / codium / vscodium
    SUBLIME = auto()  # subl / sublime_text
    JETBRAINS = auto()  # idea / pycharm / webstorm etc.
    TERMINAL_MODAL = auto()  # vi / vim / nvim / emacs / helix / kak
    TERMINAL_LINE = auto()  # nano / pico / micro
    GUI_GENERIC = auto()  # gedit / notepad / textedit / xdg-open
    CURSOR = auto()  # Cursor (special VS Code fork with extra flags)
    WINDSURF = auto()  # Windsurf (special VS Code fork)
    CODIUM = auto()  # Codium


# ---------------------------------------------------------------------------
# Per-editor metadata
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EditorMeta:
    """Static metadata for a known editor binary."""

    binary_names: tuple[str, ...]  # command names on PATH
    family: EditorFamily
    display_name: str  # human-readable
    goto_line_flag: str | None = None  # e.g. "-g", "--goto", "+"
    wait_flag: str | None = None  # e.g. "-w", "--wait"
    diff_flag: str | None = None  # e.g. "-d", "--diff"
    new_window_flag: str | None = None  # e.g. "-n", "--new-window"
    reuse_window_flag: str | None = None  # e.g. "-r", "--reuse-window"
    remote_flag: str | None = None  # e.g. "--remote"
    uses_shell_on_windows: bool = False  # launch via cmd / start
    env_hints: tuple[str, ...] = ()  # env vars that signal this editor is in use


# Registered editor metadata, ordered by preference.
_EDITOR_REGISTRY: list[EditorMeta] = [
    EditorMeta(
        ("cursor",),
        EditorFamily.CURSOR,
        "Cursor",
        goto_line_flag="-g",
        wait_flag="-w",
        diff_flag="-d",
        new_window_flag="-n",
        reuse_window_flag="-r",
        remote_flag="--remote",
        env_hints=("CURSOR_IPC_HOOK_CLI",),
    ),
    EditorMeta(
        ("windsurf",),
        EditorFamily.WINDSURF,
        "Windsurf",
        goto_line_flag="-g",
        wait_flag="-w",
        diff_flag="-d",
        new_window_flag="-n",
        reuse_window_flag="-r",
        remote_flag="--remote",
        env_hints=("WINDSURF_IPC_HOOK_CLI",),
    ),
    EditorMeta(
        ("codium", "vscodium"),
        EditorFamily.CODIUM,
        "VSCodium",
        goto_line_flag="-g",
        wait_flag="-w",
        diff_flag="-d",
        new_window_flag="-n",
        reuse_window_flag="-r",
        remote_flag="--remote",
        env_hints=(),
    ),
    EditorMeta(
        ("code", "code-insiders", "vscode"),
        EditorFamily.VSCODE,
        "VS Code",
        goto_line_flag="-g",
        wait_flag="-w",
        diff_flag="-d",
        new_window_flag="-n",
        reuse_window_flag="-r",
        remote_flag="--remote",
        env_hints=("VSCODE_IPC_HOOK_CLI", "VSCODE_GIT_ASKPASS_NODE"),
    ),
    EditorMeta(
        ("subl", "sublime_text", "sublime-text"),
        EditorFamily.SUBLIME,
        "Sublime Text",
        goto_line_flag=None,  # uses  file:line  syntax
        wait_flag="--wait",
        diff_flag="--diff",
        new_window_flag="-n",
        env_hints=(),
    ),
    EditorMeta(
        ("idea", "idea64", "intellij-idea-ultimate", "intellij-idea-community"),
        EditorFamily.JETBRAINS,
        "IntelliJ IDEA",
        goto_line_flag="--line",
        wait_flag="--wait",
        diff_flag="diff",
        env_hints=("JETBRAINS_IDE", "INTELLIJ_JDK"),
    ),
    EditorMeta(
        ("pycharm", "pycharm64"),
        EditorFamily.JETBRAINS,
        "PyCharm",
        goto_line_flag="--line",
        wait_flag="--wait",
        diff_flag="diff",
        env_hints=("PYCHARM_JDK",),
    ),
    EditorMeta(
        ("webstorm", "webstorm64"),
        EditorFamily.JETBRAINS,
        "WebStorm",
        goto_line_flag="--line",
        wait_flag="--wait",
        diff_flag="diff",
        env_hints=("WEBSTORM_JDK",),
    ),
    EditorMeta(
        ("goland", "goland64"),
        EditorFamily.JETBRAINS,
        "GoLand",
        goto_line_flag="--line",
        wait_flag="--wait",
        diff_flag="diff",
        env_hints=(),
    ),
    EditorMeta(
        ("clion", "clion64"),
        EditorFamily.JETBRAINS,
        "CLion",
        goto_line_flag="--line",
        wait_flag="--wait",
        diff_flag="diff",
        env_hints=(),
    ),
    EditorMeta(
        ("phpstorm", "phpstorm64"),
        EditorFamily.JETBRAINS,
        "PhpStorm",
        goto_line_flag="--line",
        wait_flag="--wait",
        diff_flag="diff",
        env_hints=(),
    ),
    EditorMeta(
        ("rubymine", "rubymine64"),
        EditorFamily.JETBRAINS,
        "RubyMine",
        goto_line_flag="--line",
        wait_flag="--wait",
        diff_flag="diff",
        env_hints=(),
    ),
    EditorMeta(
        ("rider", "rider64"),
        EditorFamily.JETBRAINS,
        "Rider",
        goto_line_flag="--line",
        wait_flag="--wait",
        diff_flag="diff",
        env_hints=(),
    ),
    EditorMeta(
        ("datagrip", "datagrip64"),
        EditorFamily.JETBRAINS,
        "DataGrip",
        goto_line_flag="--line",
        wait_flag="--wait",
        diff_flag="diff",
        env_hints=(),
    ),
    EditorMeta(
        ("android-studio", "studio64", "androidstudio"),
        EditorFamily.JETBRAINS,
        "Android Studio",
        goto_line_flag="--line",
        wait_flag="--wait",
        diff_flag="diff",
        env_hints=("ANDROID_STUDIO_JDK",),
    ),
    EditorMeta(
        ("nvim", "nvim-qt", "neovide"),
        EditorFamily.TERMINAL_MODAL,
        "Neovim",
        goto_line_flag="+",
        env_hints=("NVIM", "NVIM_LISTEN_ADDRESS"),
    ),
    EditorMeta(
        ("vim", "gvim", "mvim"),
        EditorFamily.TERMINAL_MODAL,
        "Vim",
        goto_line_flag="+",
        env_hints=("VIM", "VIMRUNTIME"),
    ),
    EditorMeta(
        ("vi",),
        EditorFamily.TERMINAL_MODAL,
        "vi",
        goto_line_flag="+",
        env_hints=(),
    ),
    EditorMeta(
        ("emacs", "emacsclient"),
        EditorFamily.TERMINAL_MODAL,
        "Emacs",
        goto_line_flag="+",
        env_hints=("EMACS", "INSIDE_EMACS"),
    ),
    EditorMeta(
        ("helix", "hx"),
        EditorFamily.TERMINAL_MODAL,
        "Helix",
        goto_line_flag=None,  # helix uses  file:line  syntax
        env_hints=("HELIX_RUNTIME",),
    ),
    EditorMeta(
        ("kak", "kakoune"),
        EditorFamily.TERMINAL_MODAL,
        "Kakoune",
        goto_line_flag="+",
        env_hints=(),
    ),
    EditorMeta(
        ("nano",),
        EditorFamily.TERMINAL_LINE,
        "nano",
        goto_line_flag="+",
        env_hints=(),
    ),
    EditorMeta(
        ("pico",),
        EditorFamily.TERMINAL_LINE,
        "pico",
        goto_line_flag="+",
        env_hints=(),
    ),
    EditorMeta(
        ("micro",),
        EditorFamily.TERMINAL_LINE,
        "micro",
        goto_line_flag="+",
        env_hints=(),
    ),
    EditorMeta(
        ("gedit",),
        EditorFamily.GUI_GENERIC,
        "gedit",
        goto_line_flag=None,  # gedit does not support goto-line from CLI
        wait_flag="-s",
        new_window_flag="--new-window",
        env_hints=(),
    ),
    EditorMeta(
        ("notepad++", "notepad-plus-plus"),
        EditorFamily.GUI_GENERIC,
        "Notepad++",
        goto_line_flag="-n",
        env_hints=(),
        uses_shell_on_windows=True,
    ),
    EditorMeta(
        ("notepad",),
        EditorFamily.GUI_GENERIC,
        "Notepad",
        env_hints=(),
        uses_shell_on_windows=True,
    ),
    EditorMeta(
        ("xdg-open",),
        EditorFamily.GUI_GENERIC,
        "system default",
        env_hints=(),
    ),
    EditorMeta(
        ("open",),
        EditorFamily.GUI_GENERIC,
        "macOS open",
        env_hints=(),
    ),
]


# ---------------------------------------------------------------------------
# Lookup tables (built once at import)
# ---------------------------------------------------------------------------

_BINARY_TO_META: dict[str, EditorMeta] = {}
for _m in _EDITOR_REGISTRY:
    for _bn in _m.binary_names:
        _BINARY_TO_META[_bn] = _m

# Legacy compat: map GUI editor binary names to a short family key
GUI_EDITORS: list[str] = [
    bn
    for m in _EDITOR_REGISTRY
    for bn in m.binary_names
    if m.family
    in (
        EditorFamily.VSCODE,
        EditorFamily.CURSOR,
        EditorFamily.WINDSURF,
        EditorFamily.CODIUM,
        EditorFamily.SUBLIME,
        EditorFamily.GUI_GENERIC,
    )
]

PLUS_N_EDITORS = re.compile(
    r"\b(vi|vim|nvim|nano|emacs|pico|micro|helix|hx|kak|kakoune)\b"
)

VSCODE_FAMILY: frozenset[str] = frozenset(
    bn
    for m in _EDITOR_REGISTRY
    if m.family
    in (EditorFamily.VSCODE, EditorFamily.CURSOR, EditorFamily.WINDSURF, EditorFamily.CODIUM)
    for bn in m.binary_names
)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


class EditorCapability(Enum):
    """Features an editor may advertise."""

    GOTO_LINE = auto()  # can open at a specific line
    WAIT = auto()  # blocks until file is closed
    DIFF = auto()  # can show diffs between files
    NEW_WINDOW = auto()  # can open in a new window
    REUSE_WINDOW = auto()  # can reuse an existing window
    REMOTE = auto()  # can connect to a remote editor instance


@dataclass
class EditorInfo:
    """Complete information about a detected editor."""

    command: str  # resolved binary path or name
    family: EditorFamily
    display_name: str
    meta: EditorMeta | None = None
    source: str = "env"  # "env", "git", "path", "ide_detection"
    capabilities: set[EditorCapability] = field(default_factory=set)
    extra_args: list[str] = field(default_factory=list)  # additional args from env/conf


# ---------------------------------------------------------------------------
# Non-editor blacklist — commands that are technically on PATH but should
# never be treated as text editors.
# ---------------------------------------------------------------------------

_NON_EDITOR_COMMANDS: frozenset[str] = frozenset(
    {
        "true",
        "false",
        "cat",
        "echo",
        "ls",
        "dir",
        "pwd",
        "env",
        "printenv",
        "test",
        "[",
        "sleep",
        "wait",
        "tee",
        "cp",
        "mv",
        "rm",
        "mkdir",
        "rmdir",
        "touch",
        "chmod",
        "chown",
        "ln",
        "wc",
        "head",
        "tail",
        "cut",
        "sort",
        "uniq",
        "date",
        "time",
        "yes",
        "clear",
        "which",
        "type",
        "command",
        "builtin",
        "hash",
        "exec",
        "eval",
        "source",
        "read",
        "exit",
        "return",
    }
)


def _is_non_editor(cmd: str) -> bool:
    """Return True if `cmd` is a known non-editor shell builtin/utility."""
    return _base_name(cmd) in _NON_EDITOR_COMMANDS


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------


def _shlex_split(cmd: str) -> list[str]:
    """Split a command string into argv, falling back to naive split."""
    try:
        return shlex.split(cmd)
    except ValueError:
        return cmd.split()


def _base_name(cmd: str) -> str:
    """Return the basename of the first token in a command string."""
    parts = _shlex_split(cmd)
    return Path(parts[0]).name if parts else ""


def _build_capabilities(meta: EditorMeta) -> set[EditorCapability]:
    caps: set[EditorCapability] = set()
    if meta.goto_line_flag is not None:
        caps.add(EditorCapability.GOTO_LINE)
    if meta.wait_flag is not None:
        caps.add(EditorCapability.WAIT)
    if meta.diff_flag is not None:
        caps.add(EditorCapability.DIFF)
    if meta.new_window_flag is not None:
        caps.add(EditorCapability.NEW_WINDOW)
    if meta.reuse_window_flag is not None:
        caps.add(EditorCapability.REUSE_WINDOW)
    if meta.remote_flag is not None:
        caps.add(EditorCapability.REMOTE)
    return caps


# ---------------------------------------------------------------------------
# VS Code remote / tunnel detection
# ---------------------------------------------------------------------------


def _detect_vscode_remote_cli() -> str | None:
    """
    On a VS Code Remote / tunnel session the `code` on PATH may point at an
    open-server-stdio script. Detect this and return the effective CLI path or
    None if not in a remote session.
    """
    for hint in ("VSCODE_IPC_HOOK_CLI", "VSCODE_GIT_ASKPASS_NODE"):
        v = os.environ.get(hint)
        if v and os.path.isfile(v):
            # Resolve the real 'code' sibling
            hint_dir = os.path.dirname(v)
            for candidate in ("bin/remote-cli/code", "bin/code", "code"):
                probe = os.path.join(hint_dir, candidate)
                if os.path.isfile(probe) and os.access(probe, os.X_OK):
                    return probe
    return None


def _detect_vscode_remote_cli_sync() -> str | None:
    return _detect_vscode_remote_cli()


# ---------------------------------------------------------------------------
# Editor detection
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _git_editor() -> str | None:
    """Return `git config core.editor` if set, otherwise None."""
    try:
        r = subprocess.run(
            ["git", "config", "--get", "core.editor"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def _try_resolve_editor(cmd: str) -> EditorInfo | None:
    """
    Given a raw command string (e.g. 'code -w' or 'vim'), attempt to look up
    its EditorMeta and build an EditorInfo. Returns None when the binary cannot
    be found on PATH or the command is a known non-editor.
    """
    if not cmd or not cmd.strip():
        return None
    if _is_non_editor(cmd):
        return None

    parts = _shlex_split(cmd)
    base = parts[0] if parts else ""
    extra = parts[1:] if len(parts) > 1 else []

    meta = _BINARY_TO_META.get(_base_name(base))
    if meta is None:
        # Unknown editor — treat as TERMINAL_MODAL if base is on PATH, else None
        path = which_sync(base)
        if path is None:
            return None
        # Synthesize an ad-hoc meta
        meta = EditorMeta(
            binary_names=(_base_name(base),),
            family=EditorFamily.TERMINAL_MODAL,
            display_name=_base_name(base).capitalize(),
            goto_line_flag="+",
        )

    path = which_sync(base) or base
    return EditorInfo(
        command=path,
        family=meta.family,
        display_name=meta.display_name,
        meta=meta,
        capabilities=_build_capabilities(meta),
        extra_args=extra,
    )


def detect_editors() -> list[EditorInfo]:
    """
    Return a list of all editors that are *available* on the current system,
    ordered by preference (environment overrides first, then PATH editors).

    Each entry has full metadata including capabilities.
    """
    seen_bases: set[str] = set()
    results: list[EditorInfo] = []

    def _add(info: EditorInfo | None, source: str = "path") -> None:
        if info is None:
            return
        base = _base_name(info.command)
        if base in seen_bases:
            return
        seen_bases.add(base)
        info.source = source
        results.append(info)

    # 1. VISUAL / EDITOR environment variables
    for var, src in [("VISUAL", "env_VISUAL"), ("EDITOR", "env_EDITOR")]:
        val = os.environ.get(var, "").strip()
        if val:
            _add(_try_resolve_editor(val), src)

    # 2. Git config core.editor
    git_ed = _git_editor()
    if git_ed and _base_name(git_ed) not in seen_bases:
        _add(_try_resolve_editor(git_ed), "git_config")

    # 3. GIT_EDITOR env (less common but valid)
    git_env = os.environ.get("GIT_EDITOR", "").strip()
    if git_env and _base_name(git_env) not in seen_bases:
        _add(_try_resolve_editor(git_env), "env_GIT_EDITOR")

    # 4. IDE environment hints (running inside an IDE)
    for meta in _EDITOR_REGISTRY:
        for hint in meta.env_hints:
            if os.environ.get(hint) and meta.binary_names[0] not in seen_bases:
                path = which_sync(meta.binary_names[0])
                if path:
                    _add(
                        EditorInfo(
                            command=path,
                            family=meta.family,
                            display_name=meta.display_name,
                            meta=meta,
                            source="ide_detection",
                            capabilities=_build_capabilities(meta),
                        ),
                        "ide_detection",
                    )
                break  # one hint match is enough per meta

    # 5. Scan PATH for known editors
    for meta in _EDITOR_REGISTRY:
        for bn in meta.binary_names:
            if bn in seen_bases:
                continue
            path = which_sync(bn)
            if path:
                _add(
                    EditorInfo(
                        command=path,
                        family=meta.family,
                        display_name=meta.display_name,
                        meta=meta,
                        source="path",
                        capabilities=_build_capabilities(meta),
                    ),
                    "path",
                )
                break  # first available binary for this meta

    return results


def get_external_editor_full() -> EditorInfo | None:
    """
    Like `get_external_editor()` but returns a full EditorInfo with metadata
    and capabilities instead of a raw command string.
    """
    editors = detect_editors()
    if not editors:
        return None
    # First entry is the highest-preference available editor
    return editors[0]


def get_external_editor() -> str | None:
    """
    Return the command string of the best available external editor.
    Maintains backward compatibility with the original API.
    """
    info = get_external_editor_full()
    if info is None:
        return None
    # Reconstruct command string
    parts = [info.command, *info.extra_args]
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Classification helpers
# ---------------------------------------------------------------------------


def classify_gui_editor(editor: str) -> str | None:
    """
    Given an editor command string, return a short family key if it's a known
    GUI editor, or None if it's terminal-based.

    Maintains backward compatibility.
    """
    base = _base_name(editor)
    meta = _BINARY_TO_META.get(base)
    if meta is None:
        return None
    if meta.family in (
        EditorFamily.VSCODE,
        EditorFamily.CURSOR,
        EditorFamily.WINDSURF,
        EditorFamily.CODIUM,
        EditorFamily.SUBLIME,
        EditorFamily.GUI_GENERIC,
    ):
        # Return the first binary name as the canonical key
        return meta.binary_names[0]
    return None


def is_gui_editor(editor: str) -> bool:
    """Check whether an editor command refers to a GUI editor."""
    return classify_gui_editor(editor) is not None


def is_terminal_editor(editor: str) -> bool:
    """Check whether an editor command refers to a terminal-based editor."""
    base = _base_name(editor)
    meta = _BINARY_TO_META.get(base)
    if meta is None:
        return True  # unknown editors are assumed terminal
    return meta.family in (EditorFamily.TERMINAL_MODAL, EditorFamily.TERMINAL_LINE)


def is_vscode_family(editor: str) -> bool:
    """Check if editor is a VS Code-family editor (code, cursor, windsurf, codium)."""
    return _base_name(editor) in VSCODE_FAMILY


def is_jetbrains_family(editor: str) -> bool:
    """Check if editor is a JetBrains-family IDE."""
    base = _base_name(editor)
    meta = _BINARY_TO_META.get(base)
    return meta is not None and meta.family == EditorFamily.JETBRAINS


# ---------------------------------------------------------------------------
# Goto-line argument builders
# ---------------------------------------------------------------------------


def _gui_goto_argv(
    editor_info: EditorInfo, file_path: str, line: int | None
) -> list[str]:
    """Build goto-line arguments for a GUI editor."""
    if not line:
        return [file_path]

    meta = editor_info.meta
    if meta is None:
        return [file_path]

    family = meta.family

    # VS Code family:  -g file:line
    if family in (
        EditorFamily.VSCODE,
        EditorFamily.CURSOR,
        EditorFamily.WINDSURF,
        EditorFamily.CODIUM,
    ):
        return ["-g", f"{file_path}:{line}"]

    # Sublime:  file:line
    if family == EditorFamily.SUBLIME:
        return [f"{file_path}:{line}"]

    # JetBrains:  --line N file
    if family == EditorFamily.JETBRAINS:
        return [file_path, "--line", str(line)]

    # Generic GUI: just open the file
    return [file_path]


def _terminal_goto_argv(
    editor_info: EditorInfo, file_path: str, line: int | None
) -> list[str]:
    """Build goto-line arguments for a terminal editor."""
    if not line:
        return [file_path]

    meta = editor_info.meta
    if meta is None:
        return [file_path]

    # Helix uses file:line syntax (no special flag)
    if meta.family == EditorFamily.TERMINAL_MODAL and meta.binary_names[0] in (
        "helix",
        "hx",
    ):
        return [f"{file_path}:{line}"]

    # Emacs:  +line file
    if "emacs" in meta.binary_names[0] or "emacsclient" in meta.binary_names[0]:
        return [f"+{line}", file_path]

    # Most other terminals:  +line file
    if meta.goto_line_flag == "+":
        return [f"+{line}", file_path]

    return [file_path]


# ---------------------------------------------------------------------------
# Launch helpers
# ---------------------------------------------------------------------------


def _detached_kwargs() -> dict:
    """Return subprocess keyword arguments to fully detach a child process."""
    return {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "stdin": subprocess.DEVNULL,
    }


def _spawn_detached(argv: list[str]) -> None:
    """Launch a process fully detached from the parent."""
    try:
        if sys.platform == "win32":
            cmd = " ".join(shlex.quote(a) for a in argv)
            subprocess.Popen(
                cmd,
                shell=True,  # nosec B602 — argv are shlex.quoted
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
                **_detached_kwargs(),
            )
        else:
            subprocess.Popen(argv, start_new_session=True, **_detached_kwargs())
    except OSError as e:
        log_for_debugging(f"editor spawn failed: {e}", level="error")


class _InkStub:
    """Placeholder for Ink alternate-screen handoff (no TUI in Python port)."""

    def enter_alternate_screen(self) -> None:
        pass

    def exit_alternate_screen(self) -> None:
        pass


def _get_ink_instance(_stdout) -> _InkStub | None:
    return _InkStub()


# ---------------------------------------------------------------------------
# Primary public API
# ---------------------------------------------------------------------------


def launch_editor(
    file_path: str,
    *,
    line: int | None = None,
    wait: bool = False,
    diff: tuple[str, str] | None = None,
    new_window: bool = False,
    editor: str | None = None,
) -> bool:
    """
    Launch the user's editor for `file_path`.

    Parameters
    ----------
    file_path : str
        Path to the file to open.
    line : int | None
        Optional line number to jump to.
    wait : bool
        If True, block until the editor closes (for terminal editors only;
        GUI editors always detach unless they support --wait).
    diff : tuple[str, str] | None
        If provided, open side-by-side diff of (left_file, right_file).
    new_window : bool
        Force a new editor window.
    editor : str | None
        Override the editor command. If None, auto-detect.

    Returns
    -------
    bool
        True if the editor was launched successfully.
    """
    # Resolve editor
    if editor:
        info = _try_resolve_editor(editor)
        if info is None:
            log_for_debugging(
                f"editor not found on PATH: {editor}", level="error"
            )
            return False
    else:
        info = get_external_editor_full()
        if info is None:
            log_for_debugging("no external editor found", level="error")
            return False

    meta = info.meta
    if meta is None:
        log_for_debugging(f"unknown editor: {info.command}", level="error")
        return False

    family = meta.family
    is_gui = family not in (EditorFamily.TERMINAL_MODAL, EditorFamily.TERMINAL_LINE)

    # Build argv
    argv: list[str] = [info.command, *info.extra_args]

    # Diff mode
    if diff is not None and meta.diff_flag is not None:
        if family in (
            EditorFamily.VSCODE,
            EditorFamily.CURSOR,
            EditorFamily.WINDSURF,
            EditorFamily.CODIUM,
            EditorFamily.JETBRAINS,
        ):
            argv.append(meta.diff_flag)
            argv.extend(diff)
        elif family == EditorFamily.SUBLIME:
            argv.append(meta.diff_flag)
            argv.extend(diff)
        else:
            # Fallback: open first file; diff not supported
            argv.append(diff[0])
    elif file_path:
        if is_gui:
            argv.extend(_gui_goto_argv(info, file_path, line))
        else:
            argv.extend(_terminal_goto_argv(info, file_path, line))

    # Wait flag
    if wait and meta.wait_flag:
        argv.append(meta.wait_flag)

    # New window
    if new_window and meta.new_window_flag:
        argv.append(meta.new_window_flag)

    # Launch
    if is_gui and not wait:
        # Detach GUI editors so the parent does not block
        _spawn_detached(argv)
        return True

    # Terminal editors (and GUI with --wait): run in the foreground with
    # alternate-screen handoff so the TUI is not disrupted.
    ink = _get_ink_instance(sys.stdout)
    if ink:
        ink.enter_alternate_screen()
    try:
        if sys.platform == "win32" and meta.uses_shell_on_windows:
            cmd = " ".join(shlex.quote(a) for a in argv)
            r = subprocess.run(
                cmd,
                shell=True,  # nosec B602 — argv are shlex.quoted
                stdin=sys.stdin,
                capture_output=False,
            )
        else:
            r = subprocess.run(argv, stdin=sys.stdin, capture_output=False)
        if r.returncode != 0:
            log_for_debugging(
                f"editor exited with code {r.returncode}", level="error"
            )
            return False
        return True
    except OSError as e:
        log_for_debugging(f"editor spawn failed: {e}", level="error")
        return False
    finally:
        if ink:
            ink.exit_alternate_screen()


def open_file_in_external_editor(file_path: str, line: int | None = None) -> bool:
    """
    Open a file in the user's preferred external editor.

    Backward-compatible wrapper around `launch_editor`.
    """
    return launch_editor(file_path, line=line)


def open_diff_in_editor(
    left: str,
    right: str,
    *,
    editor: str | None = None,
    new_window: bool = False,
) -> bool:
    """Open a side-by-side diff of two files in the user's editor."""
    return launch_editor("", diff=(left, right), editor=editor, new_window=new_window)


def open_directory_in_editor(
    path: str,
    *,
    editor: str | None = None,
    new_window: bool = False,
) -> bool:
    """Open a directory/folder in the user's editor (GUI editors only)."""
    return launch_editor(path, editor=editor, new_window=new_window)


# ---------------------------------------------------------------------------
# Availability & capability queries
# ---------------------------------------------------------------------------


def is_editor_available(editor: str | None = None) -> bool:
    """Check whether an editor is available (on PATH)."""
    if editor:
        return _try_resolve_editor(editor) is not None
    return get_external_editor_full() is not None


def has_capability(cap: EditorCapability, editor: str | None = None) -> bool:
    """Check whether the editor supports a specific capability."""
    if editor:
        info = _try_resolve_editor(editor)
    else:
        info = get_external_editor_full()
    return info is not None and cap in info.capabilities


def editor_display_name(editor: str | None = None) -> str:
    """Return a human-readable name for the editor."""
    if editor:
        info = _try_resolve_editor(editor)
    else:
        info = get_external_editor_full()
    if info is None:
        return "unknown"
    return info.display_name


# ---------------------------------------------------------------------------
# Pre-flight check helpers
# ---------------------------------------------------------------------------


def get_default_wait_flag(editor: str | None = None) -> str | None:
    """Return the '--wait' flag for the given editor, if supported."""
    info = _try_resolve_editor(editor) if editor else get_external_editor_full()
    if info is None or info.meta is None:
        return None
    return info.meta.wait_flag


def get_default_goto_line_flag(editor: str | None = None) -> str | None:
    """Return the goto-line flag for the given editor, if supported."""
    info = _try_resolve_editor(editor) if editor else get_external_editor_full()
    if info is None or info.meta is None:
        return None
    return info.meta.goto_line_flag


# ---------------------------------------------------------------------------
# Environment variable hints (for callers that need to set env before launch)
# ---------------------------------------------------------------------------


def editor_env_hints() -> dict[str, str]:
    """
    Return a dict of environment variables that hint at which IDE/editor the
    user is currently running inside. Useful for subprocess environments.
    """
    hints: dict[str, str] = {}
    for meta in _EDITOR_REGISTRY:
        for hint in meta.env_hints:
            val = os.environ.get(hint)
            if val:
                hints[hint] = val
    return hints


def is_running_inside_editor() -> bool:
    """
    Detect whether the current process is running inside an editor/IDE
    (e.g. VS Code integrated terminal, JetBrains terminal, Emacs).
    """
    # VS Code / Cursor / Windsurf integrated terminal
    if os.environ.get("TERM_PROGRAM") in ("vscode", "cursor", "windsurf"):
        return True
    # JetBrains
    if os.environ.get("TERMINAL_EMULATOR") == "JetBrains-JediTerm":
        return True
    # Emacs
    if os.environ.get("INSIDE_EMACS"):
        return True
    # VS Code remote indicator
    if os.environ.get("VSCODE_IPC_HOOK_CLI"):
        return True
    return False


# ---------------------------------------------------------------------------
# Exception classes
# ---------------------------------------------------------------------------


class EditorError(Exception):
    """Base exception for all editor-related errors."""

    def __init__(self, message: str, *, editor: str | None = None) -> None:
        super().__init__(message)
        self.editor = editor


class EditorNotFoundError(EditorError):
    """Raised when no suitable editor can be found."""

    def __init__(self, *, editor: str | None = None) -> None:
        msg = f"Editor not found: {editor}" if editor else "No suitable editor found"
        super().__init__(msg, editor=editor)


class EditorLaunchError(EditorError):
    """Raised when launching the editor fails."""

    def __init__(
        self,
        message: str,
        *,
        editor: str | None = None,
        returncode: int | None = None,
    ) -> None:
        super().__init__(message, editor=editor)
        self.returncode = returncode


class EditorNotSupportedError(EditorError):
    """Raised when the editor does not support a requested operation."""

    def __init__(self, operation: str, *, editor: str | None = None) -> None:
        msg = (
            f"Editor {editor} does not support {operation}"
            if editor
            else f"Operation not supported: {operation}"
        )
        super().__init__(msg, editor=editor)


class EditorTimeoutError(EditorError):
    """Raised when the editor does not close within the expected time."""

    def __init__(self, timeout: float, *, editor: str | None = None) -> None:
        msg = f"Editor did not close within {timeout:.0f}s"
        super().__init__(msg, editor=editor)
        self.timeout = timeout


# ---------------------------------------------------------------------------
# WSL detection and path conversion
# ---------------------------------------------------------------------------


def _detect_wsl() -> bool:
    """Detect whether we are running inside Windows Subsystem for Linux."""
    # Check /proc/version for "Microsoft" or "WSL"
    try:
        with open("/proc/version", "r") as f:
            content = f.read().lower()
            if "microsoft" in content or "wsl" in content:
                return True
    except OSError:
        pass
    # Check WSL_DISTRO_NAME env var (set by WSL)
    if os.environ.get("WSL_DISTRO_NAME"):
        return True
    # Check for WSL interop
    if os.environ.get("WSL_INTEROP"):
        return True
    return False


def _is_wsl_path(path: str) -> bool:
    """Check if a path is a Windows-style path when running under WSL."""
    if not _detect_wsl():
        return False
    # Windows absolute paths start with a drive letter like C:\ or D:\
    if re.match(r"^[a-zA-Z]:[\\/]", path):
        return True
    # UNC paths
    if path.startswith("\\\\") or path.startswith("//"):
        return True
    return False


def _wsl_to_windows_path(path: str) -> str:
    """Convert a WSL Linux path to a Windows path using wslpath."""
    if not _detect_wsl():
        return path
    try:
        r = subprocess.run(
            ["wslpath", "-w", path],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return path


def _windows_to_wsl_path(path: str) -> str:
    """Convert a Windows path to a WSL Linux path using wslpath."""
    if not _detect_wsl():
        return path
    try:
        r = subprocess.run(
            ["wslpath", "-u", path],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return path


def _resolve_wsl_path(path: str, *, to_windows: bool = False) -> str:
    """
    Resolve a path for the target editor context.

    When running under WSL and targeting a Windows editor (e.g. VS Code on
    Windows), convert Linux paths to Windows paths so the editor can open them.
    When targeting a Linux editor from a Windows path, convert back.
    """
    if not _detect_wsl():
        return path
    if to_windows:
        return _wsl_to_windows_path(path)
    # If path is already Windows-style, convert to WSL
    if _is_wsl_path(path):
        return _windows_to_wsl_path(path)
    return path


# ---------------------------------------------------------------------------
# IDE project / workspace detection
# ---------------------------------------------------------------------------


def _detect_vscode_workspace(path: str | None = None) -> str | None:
    """
    Detect if the given directory (or cwd) is part of a VS Code workspace.
    Returns the .code-workspace file path if found, or None.
    """
    search_path = Path(path).resolve() if path else Path.cwd()
    # Walk up looking for .vscode directory
    current: Path | None = search_path
    while current is not None:
        # Check for multi-root workspace file
        for item in current.iterdir():
            if item.is_file() and item.suffix == ".code-workspace":
                return str(item)
        # Check for .vscode directory (single-folder workspace indicator)
        vscode_dir = current / ".vscode"
        if vscode_dir.is_dir():
            # Look for workspace file inside .vscode
            for item in vscode_dir.iterdir():
                if item.suffix == ".code-workspace":
                    return str(item)
            # No explicit workspace file, but .vscode dir exists
            return str(current)
        # Move to parent
        parent = current.parent
        current = parent if parent != current else None
    return None


def _detect_jetbrains_project(path: str | None = None) -> str | None:
    """
    Detect if the given directory (or cwd) is a JetBrains IDE project.
    Returns the project directory path if found, or None.
    """
    search_path = Path(path).resolve() if path else Path.cwd()
    jetbrains_dir = search_path / ".idea"
    if jetbrains_dir.is_dir():
        return str(search_path)
    # Check parent directories (up to 3 levels)
    current: Path | None = search_path
    for _ in range(3):
        parent = current.parent
        if parent == current:
            break
        current = parent
        if (current / ".idea").is_dir():
            return str(current)
    return None


def detect_ide_project(path: str | None = None) -> dict[str, str | None]:
    """
    Detect what IDE project (if any) the given directory belongs to.

    Returns a dict with keys like 'vscode_workspace', 'jetbrains_project'.
    """
    return {
        "vscode_workspace": _detect_vscode_workspace(path),
        "jetbrains_project": _detect_jetbrains_project(path),
    }


# ---------------------------------------------------------------------------
# Robust binary resolution
# ---------------------------------------------------------------------------


def _resolve_editor_binary(binary_name: str) -> str | None:
    """
    Resolve an editor binary name to a full path, trying multiple strategies:

    1. Direct PATH lookup via shutil.which
    2. Check common install locations (platform-specific)
    3. Follow symlinks to resolve wrapper scripts

    Returns the resolved path or None.
    """
    # 1. Standard PATH lookup
    resolved = shutil.which(binary_name)
    if resolved:
        # Resolve symlinks to get the real binary
        try:
            resolved = str(Path(resolved).resolve())
        except OSError:
            pass
        if os.path.isfile(resolved) and os.access(resolved, os.X_OK):
            return resolved

    # 2. Platform-specific install locations
    if sys.platform == "darwin":
        resolved = _resolve_macos_binary(binary_name)
        if resolved:
            return resolved
    elif sys.platform == "win32":
        resolved = _resolve_windows_binary(binary_name)
        if resolved:
            return resolved
    else:
        resolved = _resolve_linux_binary(binary_name)
        if resolved:
            return resolved

    # 3. Try flatpak / snap paths on Linux
    if sys.platform != "win32":
        flatpak_path = _resolve_flatpak_binary(binary_name)
        if flatpak_path:
            return flatpak_path
        snap_path = _resolve_snap_binary(binary_name)
        if snap_path:
            return snap_path

    return None


def _resolve_macos_binary(binary_name: str) -> str | None:
    """Search macOS-specific application paths."""
    candidates: list[str] = []
    # Map of known macOS app names to their CLI paths
    macos_app_map: dict[str, list[str]] = {
        "code": [
            "/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code",
        ],
        "cursor": [
            "/Applications/Cursor.app/Contents/Resources/app/bin/cursor",
        ],
        "windsurf": [
            "/Applications/Windsurf.app/Contents/Resources/app/bin/windsurf",
        ],
        "subl": [
            "/Applications/Sublime Text.app/Contents/SharedSupport/bin/subl",
        ],
        "sublime_text": [
            "/Applications/Sublime Text.app/Contents/SharedSupport/bin/subl",
        ],
        "idea": [
            "/Applications/IntelliJ IDEA.app/Contents/MacOS/idea",
        ],
    }

    for candidate in macos_app_map.get(binary_name, []):
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate

    # Generic search in /Applications and ~/Applications
    search_dirs = [
        Path("/Applications"),
        Path.home() / "Applications",
    ]
    for search_dir in search_dirs:
        if not search_dir.is_dir():
            continue
        for item in search_dir.iterdir():
            if not item.is_dir():
                continue
            if item.suffix == ".app":
                cli_path = item / "Contents" / "Resources" / "app" / "bin" / binary_name
                if cli_path.is_file() and os.access(cli_path, os.X_OK):
                    return str(cli_path)

    return None


def _resolve_windows_binary(binary_name: str) -> str | None:
    """Search Windows-specific install locations."""
    candidates: list[str] = []
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    program_files = os.environ.get("ProgramFiles", "C:\\Program Files")
    program_files_x86 = os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)")

    windows_paths: dict[str, list[str]] = {
        "code": [
            f"{local_app_data}\\Programs\\Microsoft VS Code\\bin\\code.cmd",
            f"{program_files}\\Microsoft VS Code\\bin\\code.cmd",
        ],
        "cursor": [
            f"{local_app_data}\\Programs\\Cursor\\Cursor.exe",
        ],
    }

    for candidate in windows_paths.get(binary_name, []):
        if os.path.isfile(candidate):
            return candidate

    return None


def _resolve_linux_binary(binary_name: str) -> str | None:
    """Search Linux-specific install locations."""
    candidates: list[str] = []
    home = str(Path.home())

    linux_paths: dict[str, list[str]] = {
        "code": [
            f"{home}/.local/share/code/code",
            "/usr/share/code/bin/code",
            "/usr/lib/code/bin/code",
        ],
        "cursor": [
            f"{home}/.local/share/cursor/cursor",
        ],
    }

    for candidate in linux_paths.get(binary_name, []):
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate

    return None


def _resolve_flatpak_binary(binary_name: str) -> str | None:
    """Try to find the binary as a flatpak installation."""
    flatpak_map = {
        "code": ["com.visualstudio.code"],
        "codium": ["com.vscodium.codium"],
    }
    app_ids = flatpak_map.get(binary_name, [])
    for app_id in app_ids:
        try:
            r = subprocess.run(
                ["flatpak", "run", "--command=which", app_id, binary_name],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if r.returncode == 0 and r.stdout.strip():
                path = r.stdout.strip()
                if os.path.isfile(path):
                    return path
        except (OSError, subprocess.TimeoutExpired):
            pass
        # Also try direct flatpak run
        flatpak_bin = shutil.which("flatpak")
        if flatpak_bin:
            return flatpak_bin  # Caller can use flatpak run
    return None


def _resolve_snap_binary(binary_name: str) -> str | None:
    """Try to find the binary as a snap installation."""
    snap_map = {
        "code": "code",
        "codium": "codium",
        "nvim": "nvim",
    }
    snap_name = snap_map.get(binary_name)
    if not snap_name:
        return None
    snap_bin = f"/snap/bin/{snap_name}"
    if os.path.isfile(snap_bin) and os.access(snap_bin, os.X_OK):
        return snap_bin
    return None


# ---------------------------------------------------------------------------
# Path validation
# ---------------------------------------------------------------------------


def _validate_file_path(file_path: str) -> str:
    """
    Validate and normalize a file path for editor launching.

    - Expands ~ and environment variables
    - Resolves to absolute path
    - Checks file exists (returns path even if not — editor may create it)
    - Handles WSL path conversion
    """
    # Expand user and vars
    expanded = os.path.expandvars(os.path.expanduser(file_path))
    # Resolve to absolute
    if not os.path.isabs(expanded):
        expanded = str(Path(expanded).resolve())
    # Normalize separators
    normalized = os.path.normpath(expanded)
    # WSL handling
    if _detect_wsl() and _is_wsl_path(normalized):
        normalized = _windows_to_wsl_path(normalized)
    return normalized


def _validate_directory_path(dir_path: str) -> str:
    """Validate and normalize a directory path for editor launching."""
    expanded = os.path.expandvars(os.path.expanduser(dir_path))
    if not os.path.isabs(expanded):
        expanded = str(Path(expanded).resolve())
    normalized = os.path.normpath(expanded)
    return normalized


# ---------------------------------------------------------------------------
# Temporary file management for content editing
# ---------------------------------------------------------------------------


def _create_editor_tempfile(
    content: str,
    *,
    suffix: str = ".txt",
    prefix: str = "editor-",
    directory: str | None = None,
) -> str:
    """
    Create a temporary file with the given content for editor editing.

    Returns the path to the created temp file. Caller is responsible for cleanup.
    """
    fd, path = tempfile.mkstemp(suffix=suffix, prefix=prefix, dir=directory, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
    except OSError:
        # Clean up on write failure
        try:
            os.unlink(path)
        except OSError:
            pass
        raise
    return path


def _cleanup_tempfile(path: str) -> None:
    """Safely remove a temporary file, ignoring errors."""
    try:
        if os.path.isfile(path):
            os.unlink(path)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Convenience capability queries
# ---------------------------------------------------------------------------


def editor_supports_wait(editor: str | None = None) -> bool:
    """Check whether the editor supports --wait / blocking mode."""
    return has_capability(EditorCapability.WAIT, editor)


def editor_supports_diff(editor: str | None = None) -> bool:
    """Check whether the editor supports diff mode."""
    return has_capability(EditorCapability.DIFF, editor)


def editor_supports_goto_line(editor: str | None = None) -> bool:
    """Check whether the editor supports opening at a specific line."""
    return has_capability(EditorCapability.GOTO_LINE, editor)


def editor_supports_new_window(editor: str | None = None) -> bool:
    """Check whether the editor supports opening in a new window."""
    return has_capability(EditorCapability.NEW_WINDOW, editor)


def editor_supports_remote(editor: str | None = None) -> bool:
    """Check whether the editor supports remote connections."""
    return has_capability(EditorCapability.REMOTE, editor)


# ---------------------------------------------------------------------------
# Editor command formatting
# ---------------------------------------------------------------------------


def get_editor_command(editor: str | None = None) -> str | None:
    """
    Get the resolved command string for the best available editor.

    Returns just the binary path (no extra args), suitable for use as a
    command name in subprocess calls.
    """
    if editor:
        info = _try_resolve_editor(editor)
    else:
        info = get_external_editor_full()
    return info.command if info else None


def get_editor_args_for_file(
    file_path: str,
    *,
    line: int | None = None,
    diff: tuple[str, str] | None = None,
    diff_label: str | None = None,
    new_window: bool = False,
    editor: str | None = None,
) -> list[str]:
    """
    Build the complete argv list for opening a file in an editor.

    This is the composable version of `launch_editor` — it builds the argv
    but does not launch. Useful for building compound commands or logging.
    """
    if editor:
        info = _try_resolve_editor(editor)
        if info is None:
            raise EditorNotFoundError(editor=editor)
    else:
        info = get_external_editor_full()
        if info is None:
            raise EditorNotFoundError()

    meta = info.meta
    if meta is None:
        raise EditorError(f"Unknown editor: {info.command}")

    family = meta.family
    is_gui = family not in (EditorFamily.TERMINAL_MODAL, EditorFamily.TERMINAL_LINE)

    argv: list[str] = [info.command, *info.extra_args]
    normalized = _validate_file_path(file_path)

    # Diff mode takes priority
    if diff is not None and meta.diff_flag is not None:
        if family in (
            EditorFamily.VSCODE,
            EditorFamily.CURSOR,
            EditorFamily.WINDSURF,
            EditorFamily.CODIUM,
            EditorFamily.JETBRAINS,
        ):
            argv.append(meta.diff_flag)
            argv.extend(diff)
        elif family == EditorFamily.SUBLIME:
            argv.append(meta.diff_flag)
            argv.extend(diff)
        else:
            argv.append(diff[0])
    elif normalized:
        if is_gui:
            argv.extend(_gui_goto_argv(info, normalized, line))
        else:
            argv.extend(_terminal_goto_argv(info, normalized, line))

    if new_window and meta.new_window_flag:
        argv.append(meta.new_window_flag)

    return argv


def format_editor_command(
    file_path: str,
    *,
    line: int | None = None,
    editor: str | None = None,
    wait: bool = False,
) -> str:
    """
    Format a human-readable editor command string for display or logging.

    Example: "code -g /path/to/file:42"
    """
    try:
        argv = get_editor_args_for_file(file_path, line=line, editor=editor)
    except EditorError:
        return f"<editor not found> {file_path}"
    if wait:
        info = _try_resolve_editor(editor) if editor else get_external_editor_full()
        if info and info.meta and info.meta.wait_flag:
            argv.append(info.meta.wait_flag)
    return " ".join(shlex.quote(a) for a in argv)


# ---------------------------------------------------------------------------
# Editor name list
# ---------------------------------------------------------------------------


def get_available_editor_names() -> list[str]:
    """
    Return a list of human-readable names for all available editors on the
    system, ordered by preference.

    Simpler than `detect_editors()` — no metadata, just names.
    """
    return [e.display_name for e in detect_editors()]


def list_available_editors() -> list[EditorInfo]:
    """
    Return the full list of available editors with metadata.

    Alias for `detect_editors()` with a more self-documenting name.
    """
    return detect_editors()


# ---------------------------------------------------------------------------
# Fallback editor launching
# ---------------------------------------------------------------------------


def _try_launch_with_fallbacks(
    file_path: str,
    *,
    line: int | None = None,
    wait: bool = False,
    diff: tuple[str, str] | None = None,
    new_window: bool = False,
    editors: list[str] | None = None,
) -> bool:
    """
    Try launching a file with a chain of editors, falling back to the next
    if the previous one is not available.

    Parameters
    ----------
    editors : list[str] | None
        Ordered list of editor commands to try. If None, uses auto-detection
        (tries the best available editor).
    """
    if editors is None:
        # Single editor from auto-detection
        return launch_editor(file_path, line=line, wait=wait, diff=diff,
                            new_window=new_window)

    for ed in editors:
        info = _try_resolve_editor(ed)
        if info is not None:
            result = launch_editor(
                file_path, line=line, wait=wait, diff=diff,
                new_window=new_window, editor=ed,
            )
            if result:
                return True
    return False


# ---------------------------------------------------------------------------
# Launch editor and wait (convenience wrapper)
# ---------------------------------------------------------------------------


def launch_editor_wait(
    file_path: str,
    *,
    line: int | None = None,
    editor: str | None = None,
) -> bool:
    """
    Launch the editor and block until the user closes it.

    Convenience wrapper around `launch_editor` with wait=True.
    For GUI editors, this requires --wait support.
    """
    return launch_editor(file_path, line=line, wait=True, editor=editor)


# ---------------------------------------------------------------------------
# Multi-file opening
# ---------------------------------------------------------------------------


def open_multiple_files(
    file_paths: list[str],
    *,
    editor: str | None = None,
    line: int | None = None,
    new_window: bool = False,
) -> bool:
    """
    Open multiple files in the editor.

    For editors that support opening multiple files from the command line
    (VS Code, Sublime, JetBrains, terminal editors), all files are passed
    as arguments. For editors that don't, opens them one by one.

    Parameters
    ----------
    file_paths : list[str]
        List of file paths to open.
    editor : str | None
        Editor override.
    line : int | None
        Line number (applied to the first file only).
    new_window : bool
        Force a new window.

    Returns
    -------
    bool
        True if at least one file was opened successfully.
    """
    if not file_paths:
        return False

    if editor:
        info = _try_resolve_editor(editor)
    else:
        info = get_external_editor_full()

    if info is None or info.meta is None:
        return False

    family = info.meta.family
    multi_file_families = {
        EditorFamily.VSCODE,
        EditorFamily.CURSOR,
        EditorFamily.WINDSURF,
        EditorFamily.CODIUM,
        EditorFamily.SUBLIME,
        EditorFamily.JETBRAINS,
        EditorFamily.TERMINAL_MODAL,
        EditorFamily.TERMINAL_LINE,
    }

    if family in multi_file_families:
        # Build argv for all files
        argv: list[str] = [info.command, *info.extra_args]
        if new_window and info.meta.new_window_flag:
            argv.append(info.meta.new_window_flag)

        if len(file_paths) >= 1:
            first = _validate_file_path(file_paths[0])
            rest = [_validate_file_path(p) for p in file_paths[1:]]
            if line and EditorCapability.GOTO_LINE in info.capabilities:
                # Build goto-line for the first file
                is_gui = family not in (
                    EditorFamily.TERMINAL_MODAL,
                    EditorFamily.TERMINAL_LINE,
                )
                if is_gui:
                    argv.extend(_gui_goto_argv(info, first, line))
                else:
                    argv.extend(_terminal_goto_argv(info, first, line))
            else:
                argv.append(first)
            argv.extend(rest)

        is_gui = family not in (EditorFamily.TERMINAL_MODAL, EditorFamily.TERMINAL_LINE)
        if is_gui:
            _spawn_detached(argv)
            return True
        else:
            try:
                subprocess.run(argv, stdin=sys.stdin, capture_output=False)
                return True
            except OSError as e:
                log_for_debugging(f"multi-file editor spawn failed: {e}", level="error")
                return False
    else:
        # Sequential opening for editors that don't support multi-file
        success = False
        for i, fp in enumerate(file_paths):
            fline = line if i == 0 else None
            if launch_editor(fp, line=fline, editor=editor, new_window=new_window):
                success = True
        return success


# ---------------------------------------------------------------------------
# Content editing in external editor (the core workflow)
# ---------------------------------------------------------------------------


def edit_content_in_editor(
    content: str,
    *,
    suffix: str = ".txt",
    editor: str | None = None,
    wait: bool = True,
) -> str | None:
    """
    Write content to a temporary file, launch the editor, and read back the
    (possibly changed) content after the editor closes.

    This is the fundamental "edit in external editor" workflow.

    Parameters
    ----------
    content : str
        The initial content to edit.
    suffix : str
        File extension for the temp file (default .txt). Use .py, .json, etc.
        to get syntax highlighting in the editor.
    editor : str | None
        Editor override.
    wait : bool
        Whether to block until the editor closes (default True).

    Returns
    -------
    str | None
        The edited content, or None if the editor could not be launched or
        the file could not be read back.
    """
    try:
        tmp_path = _create_editor_tempfile(content, suffix=suffix)
    except OSError as e:
        log_for_debugging(f"failed to create editor temp file: {e}", level="error")
        return None

    try:
        success = launch_editor(tmp_path, wait=wait, editor=editor)
        if not success:
            return None
        # Read back the (potentially modified) content
        try:
            with open(tmp_path, "r", encoding="utf-8") as f:
                edited = f.read()
            # Normalize line endings: keep platform-native for the caller
            return edited
        except OSError as e:
            log_for_debugging(
                f"failed to read back edited file: {e}", level="error"
            )
            return None
    finally:
        _cleanup_tempfile(tmp_path)


def edit_file_in_editor_v2(
    file_path: str,
    *,
    editor: str | None = None,
    wait: bool = True,
) -> str | None:
    """
    Open an existing file in the editor and return its (possibly modified)
    contents after the editor closes.

    Unlike `edit_content_in_editor`, this modifies an existing file in place
    and reads it back.

    Parameters
    ----------
    file_path : str
        Path to the existing file to edit.
    editor : str | None
        Editor override.
    wait : bool
        Whether to block until the editor closes.

    Returns
    -------
    str | None
        The file contents after editing, or None on failure.
    """
    normalized = _validate_file_path(file_path)
    if not os.path.isfile(normalized):
        log_for_debugging(
            f"file not found for editor: {normalized}", level="error"
        )
        return None
    # Capture original mtime
    try:
        orig_mtime = os.path.getmtime(normalized)
    except OSError:
        orig_mtime = None
    success = launch_editor(normalized, wait=wait, editor=editor)
    if not success:
        return None
    try:
        new_mtime = os.path.getmtime(normalized)
        if orig_mtime is not None and new_mtime == orig_mtime:
            # File not modified; read anyway
            pass
        with open(normalized, "r", encoding="utf-8") as f:
            return f.read()
    except OSError as e:
        log_for_debugging(
            f"failed to read back file after editing: {e}", level="error"
        )
        return None


# ---------------------------------------------------------------------------
# Async editor launch
# ---------------------------------------------------------------------------


async def launch_editor_async(
    file_path: str,
    *,
    line: int | None = None,
    wait: bool = False,
    diff: tuple[str, str] | None = None,
    new_window: bool = False,
    editor: str | None = None,
) -> bool:
    """
    Async version of `launch_editor`.

    For detached (GUI, non-wait) launches, returns immediately.
    For blocking (terminal, wait) launches, runs via asyncio subprocess.
    """
    import asyncio

    if editor:
        info = _try_resolve_editor(editor)
        if info is None:
            log_for_debugging(
                f"editor not found on PATH: {editor}", level="error"
            )
            return False
    else:
        info = get_external_editor_full()
        if info is None:
            log_for_debugging("no external editor found", level="error")
            return False

    meta = info.meta
    if meta is None:
        log_for_debugging(f"unknown editor: {info.command}", level="error")
        return False

    family = meta.family
    is_gui = family not in (EditorFamily.TERMINAL_MODAL, EditorFamily.TERMINAL_LINE)

    # Build argv
    argv: list[str] = [info.command, *info.extra_args]
    if diff is not None and meta.diff_flag is not None:
        if family in (
            EditorFamily.VSCODE,
            EditorFamily.CURSOR,
            EditorFamily.WINDSURF,
            EditorFamily.CODIUM,
            EditorFamily.JETBRAINS,
        ):
            argv.append(meta.diff_flag)
            argv.extend(diff)
        elif family == EditorFamily.SUBLIME:
            argv.append(meta.diff_flag)
            argv.extend(diff)
        else:
            argv.append(diff[0])
    elif file_path:
        if is_gui:
            argv.extend(_gui_goto_argv(info, file_path, line))
        else:
            argv.extend(_terminal_goto_argv(info, file_path, line))

    if wait and meta.wait_flag:
        argv.append(meta.wait_flag)
    if new_window and meta.new_window_flag:
        argv.append(meta.new_window_flag)

    # Launch
    if is_gui and not wait:
        _spawn_detached(argv)
        return True

    # Terminal editors: run via asyncio subprocess
    try:
        if sys.platform == "win32" and meta.uses_shell_on_windows:
            cmd = " ".join(shlex.quote(a) for a in argv)
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdin=asyncio.subprocess.PIPE,
            )
        else:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE,
            )

        await proc.communicate()
        if proc.returncode != 0:
            log_for_debugging(
                f"editor exited with code {proc.returncode}", level="error"
            )
            return False
        return True
    except OSError as e:
        log_for_debugging(f"editor async spawn failed: {e}", level="error")
        return False


async def edit_content_in_editor_async(
    content: str,
    *,
    suffix: str = ".txt",
    editor: str | None = None,
    wait: bool = True,
) -> str | None:
    """
    Async version of `edit_content_in_editor`.

    Write content to a temp file, launch editor asynchronously, read back.
    """
    try:
        tmp_path = _create_editor_tempfile(content, suffix=suffix)
    except OSError as e:
        log_for_debugging(f"failed to create editor temp file: {e}", level="error")
        return None

    try:
        success = await launch_editor_async(tmp_path, wait=wait, editor=editor)
        if not success:
            return None
        try:
            with open(tmp_path, "r", encoding="utf-8") as f:
                return f.read()
        except OSError as e:
            log_for_debugging(
                f"failed to read back edited file: {e}", level="error"
            )
            return None
    finally:
        _cleanup_tempfile(tmp_path)


# ---------------------------------------------------------------------------
# Editor timeout management
# ---------------------------------------------------------------------------


class _EditorTimeoutManager:
    """
    Context manager that warns if an editor session exceeds a timeout.

    Usage:
        with _EditorTimeoutManager(timeout=300, file_path="/tmp/foo.txt"):
            launch_editor("/tmp/foo.txt", wait=True)
    """

    def __init__(
        self,
        timeout: float = 300,
        file_path: str = "",
        editor: str | None = None,
    ) -> None:
        self._timeout = timeout
        self._file_path = file_path
        self._editor = editor

    def __enter__(self) -> "_EditorTimeoutManager":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass  # Timeout enforcement is best-effort; callers handle process lifecycle


def _launch_with_timeout(
    file_path: str,
    *,
    timeout: float = 300,
    line: int | None = None,
    editor: str | None = None,
) -> bool:
    """
    Launch the editor with a soft timeout. If the editor does not return
    within `timeout` seconds, log a warning but do not kill the process.

    Note: This does NOT enforce a hard timeout (killing the editor would
    lose user work). It is informational only.
    """
    import threading

    warned: list[bool] = [False]

    def _warn() -> None:
        warned[0] = True
        log_for_debugging(
            f"Editor still open after {timeout:.0f}s for {file_path}. "
            f"Waiting for editor to close...",
            level="warn",
        )

    timer = threading.Timer(timeout, _warn)
    timer.daemon = True
    timer.start()

    try:
        result = launch_editor(file_path, line=line, wait=True, editor=editor)
        return result
    finally:
        timer.cancel()


# ---------------------------------------------------------------------------
# get_external_editor_or_raise — variant that raises instead of returning None
# ---------------------------------------------------------------------------


def get_external_editor_or_raise() -> EditorInfo:
    """
    Like `get_external_editor_full()` but raises `EditorNotFoundError` instead
    of returning None when no editor is available.
    """
    info = get_external_editor_full()
    if info is None:
        raise EditorNotFoundError()
    return info


# ---------------------------------------------------------------------------
# Editor binary lookup by family
# ---------------------------------------------------------------------------


def find_editor_by_family(family: EditorFamily) -> EditorInfo | None:
    """
    Find the best available editor belonging to the given family.

    Useful when you need a specific class of editor (e.g. "any JetBrains IDE"
    or "any terminal modal editor").
    """
    for editor in detect_editors():
        if editor.family == family:
            return editor
    return None


def find_editor_by_capability(
    capability: EditorCapability,
    *,
    exclude_families: set[EditorFamily] | None = None,
) -> EditorInfo | None:
    """
    Find the best available editor that supports a specific capability,
    optionally excluding certain families.
    """
    exclude = exclude_families or set()
    for editor in detect_editors():
        if capability in editor.capabilities and editor.family not in exclude:
            return editor
    return None


# ---------------------------------------------------------------------------
# Plugin detection helpers — check if editor has specific plugins/extensions
# ---------------------------------------------------------------------------


def _detect_vscode_extension(extension_id: str) -> bool:
    """
    Check if a specific VS Code extension is installed.

    Parameters
    ----------
    extension_id : str
        Extension ID in the format "publisher.extension" (e.g. "ms-python.python").

    Returns
    -------
    bool
        True if the extension appears to be installed.
    """
    code_bin = which_sync("code") or which_sync("code-insiders")
    if not code_bin:
        return False
    try:
        r = subprocess.run(
            [code_bin, "--list-extensions"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if r.returncode == 0:
            installed = set(r.stdout.strip().splitlines())
            return extension_id.lower() in {ext.lower() for ext in installed}
    except (OSError, subprocess.TimeoutExpired):
        pass
    return False


# ---------------------------------------------------------------------------
# Advanced launch: open at a specific column (where supported)
# ---------------------------------------------------------------------------


def _goto_line_column_argv(
    editor_info: EditorInfo, file_path: str, line: int, column: int
) -> list[str]:
    """
    Build argv for opening a file at a specific line AND column.

    Only supported by VS Code family and a few others.
    """
    meta = editor_info.meta
    if meta is None:
        return [file_path]

    family = meta.family
    if family in (
        EditorFamily.VSCODE,
        EditorFamily.CURSOR,
        EditorFamily.WINDSURF,
        EditorFamily.CODIUM,
    ):
        return ["-g", f"{file_path}:{line}:{column}"]
    if family == EditorFamily.SUBLIME:
        return [f"{file_path}:{line}:{column}"]
    if family == EditorFamily.JETBRAINS:
        return [file_path, "--line", str(line), "--column", str(column)]
    if family in (EditorFamily.TERMINAL_MODAL, EditorFamily.TERMINAL_LINE):
        if meta.goto_line_flag == "+":
            # Most terminal editors: +line, then use normal on column
            return [f"+{line}", file_path, f"+normal{column}G"]
        return [file_path]

    return [file_path]


def launch_editor_at_position(
    file_path: str,
    *,
    line: int,
    column: int = 0,
    wait: bool = False,
    editor: str | None = None,
) -> bool:
    """
    Launch the editor and jump to a specific (line, column) position.

    When column is 0, behaves like `launch_editor` with a line number.
    """
    if editor:
        info = _try_resolve_editor(editor)
        if info is None:
            log_for_debugging(
                f"editor not found on PATH: {editor}", level="error"
            )
            return False
    else:
        info = get_external_editor_full()
        if info is None:
            log_for_debugging("no external editor found", level="error")
            return False

    meta = info.meta
    if meta is None:
        log_for_debugging(f"unknown editor: {info.command}", level="error")
        return False

    family = meta.family
    is_gui = family not in (EditorFamily.TERMINAL_MODAL, EditorFamily.TERMINAL_LINE)

    normalized = _validate_file_path(file_path)
    argv: list[str] = [info.command, *info.extra_args]

    if column > 0:
        argv.extend(_goto_line_column_argv(info, normalized, line, column))
    elif line > 0:
        if is_gui:
            argv.extend(_gui_goto_argv(info, normalized, line))
        else:
            argv.extend(_terminal_goto_argv(info, normalized, line))
    else:
        argv.append(normalized)

    if wait and meta.wait_flag:
        argv.append(meta.wait_flag)

    if is_gui and not wait:
        _spawn_detached(argv)
        return True

    ink = _get_ink_instance(sys.stdout)
    if ink:
        ink.enter_alternate_screen()
    try:
        if sys.platform == "win32" and meta.uses_shell_on_windows:
            cmd = " ".join(shlex.quote(a) for a in argv)
            r = subprocess.run(
                cmd,
                shell=True,
                stdin=sys.stdin,
                capture_output=False,
            )
        else:
            r = subprocess.run(argv, stdin=sys.stdin, capture_output=False)
        if r.returncode != 0:
            log_for_debugging(
                f"editor exited with code {r.returncode}", level="error"
            )
            return False
        return True
    except OSError as e:
        log_for_debugging(f"editor spawn failed: {e}", level="error")
        return False
    finally:
        if ink:
            ink.exit_alternate_screen()


# ---------------------------------------------------------------------------
# Bare editor launch (no file, just open the editor)
# ---------------------------------------------------------------------------


def launch_editor_bare(
    *,
    editor: str | None = None,
    new_window: bool = True,
    directory: str | None = None,
) -> bool:
    """
    Launch the editor without opening a specific file.

    Useful for opening the editor's welcome screen, or opening a directory.

    Parameters
    ----------
    editor : str | None
        Editor override.
    new_window : bool
        Force a new window (usually desired for bare launches).
    directory : str | None
        Optional directory to open in the editor.

    Returns
    -------
    bool
        True if the editor was launched.
    """
    if editor:
        info = _try_resolve_editor(editor)
    else:
        info = get_external_editor_full()

    if info is None or info.meta is None:
        log_for_debugging("no editor available for bare launch", level="error")
        return False

    argv: list[str] = [info.command, *info.extra_args]

    if new_window and info.meta.new_window_flag:
        argv.append(info.meta.new_window_flag)

    if directory:
        normalized_dir = _validate_directory_path(directory)
        argv.append(normalized_dir)

    family = info.meta.family
    is_gui = family not in (EditorFamily.TERMINAL_MODAL, EditorFamily.TERMINAL_LINE)

    if is_gui:
        _spawn_detached(argv)
        return True

    # Terminal editors with no file: just launch in foreground
    ink = _get_ink_instance(sys.stdout)
    if ink:
        ink.enter_alternate_screen()
    try:
        subprocess.run(argv, stdin=sys.stdin, capture_output=False)
        return True
    except OSError as e:
        log_for_debugging(f"bare editor spawn failed: {e}", level="error")
        return False
    finally:
        if ink:
            ink.exit_alternate_screen()


# ---------------------------------------------------------------------------
# System editor command (sensible-editor / editor / vi fallback)
# ---------------------------------------------------------------------------


def get_system_editor() -> str:
    """
    Get the system default editor command, following the standard precedence:

    1. $VISUAL
    2. $EDITOR
    3. 'sensible-editor' (Debian/Ubuntu)
    4. 'editor' (POSIX)
    5. 'vi' (ultimate fallback)

    This returns the raw command string, not an EditorInfo.
    """
    for var in ("VISUAL", "EDITOR"):
        val = os.environ.get(var, "").strip()
        if val:
            return val

    for fallback in ("sensible-editor", "editor", "vi"):
        if shutil.which(fallback):
            return fallback

    return "vi"  # ultimate fallback: POSIX guarantees vi exists


def launch_system_editor(
    file_path: str,
    *,
    line: int | None = None,
    wait: bool = True,
) -> bool:
    """
    Launch the system default editor (via $VISUAL / $EDITOR / sensible-editor).

    This bypasses the smart editor detection and uses the system-configured
    editor, which is the standard Unix behavior.
    """
    system_editor = get_system_editor()
    return launch_editor(file_path, line=line, wait=wait, editor=system_editor)
