"""Load commands/agents/skills markdown — port of `markdownConfigLoader.ts` (complete).

Discovers and parses `.md` files from managed (policy), user, and project directories
under `.hare/{commands,agents,output-styles,skills,workflows}`. Handles:
  - ripgrep-based file search with native-fs fallback
  - symlink-aware traversal with cycle detection
  - inode-based deduplication (same physical file via different paths)
  - git worktree → main-repo fallback for missing subdirectories
  - plugin-only policy gating (strictPluginOnlyCustomization)
  - source-guarded loading (isSettingSourceEnabled)
  - memoization keyed by (subdir, cwd)
"""

from __future__ import annotations

import asyncio
import os
import stat as stat_module
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from hare.utils.debug import log_for_debugging
from hare.utils.env_utils import get_hare_config_home_dir, is_env_truthy
from hare.utils.errors import is_fs_inaccessible
from hare.utils.frontmatter_parser import parse_frontmatter
from hare.utils.git_utils import find_canonical_git_root, find_git_root
from hare.utils.memoize import memoize_with_ttl_async
from hare.utils.ripgrep import rip_grep

# ---------------------------------------------------------------------------
# Constants & types
# ---------------------------------------------------------------------------

CLAUDE_CONFIG_DIRECTORIES: tuple[str, ...] = (
    "commands",
    "agents",
    "output-styles",
    "skills",
    "workflows",
)

ClaudeConfigDirectory = Literal[
    "commands", "agents", "output-styles", "skills", "workflows"
]

SettingSource = Literal["policySettings", "userSettings", "projectSettings"]

# ---------------------------------------------------------------------------
# File-search timeout (matches TS: AbortSignal.timeout(3000))
# ---------------------------------------------------------------------------

_RIPGREP_TIMEOUT_MS = 3000


# =============================================================================
# MarkdownFile
# =============================================================================

@dataclass
class MarkdownFile:
    file_path: str
    base_dir: str
    frontmatter: dict[str, Any]
    content: str
    source: SettingSource


# =============================================================================
# Path helpers
# =============================================================================


def normalize_path_for_comparison(p: str) -> str:
    """Normalize a path for platform-safe comparison.

    TS:  normalizePathForComparison → NFC normalization + lower-case on Windows.
    We NFC-normalize (matching the TS behaviour on macOS/Linux). On case-insensitive
    file-systems the os-fs layer already handles collisions; the normalisation here
    is for consistent string comparisons, not access.
    """
    return unicodedata.normalize("NFC", os.path.normpath(p))


# =============================================================================
# File identity (inode deduplication)
# =============================================================================


async def get_file_identity(file_path: str) -> str | None:
    """Return a unique ``"dev:ino"`` identity for the file, or *None*.

    TS:  getFileIdentity — uses lstat with ``bigint: true``.

    We return *None* when:
      * the file doesn't exist / can't be stat'd
      * the filesystem reports ``dev=0`` and ``ino=0`` (NFS, FUSE, network mounts
        where inode-based deduplication is unreliable)
    """
    try:
        st = os.lstat(file_path)
    except OSError:
        return None

    dev = getattr(st, "st_dev", 0)
    ino = getattr(st, "st_ino", 0)
    if dev == 0 and ino == 0:
        return None
    return f"{dev}:{ino}"


# =============================================================================
# Git-root boundary resolution
# =============================================================================


def resolve_stop_boundary(cwd: str) -> str | None:
    """Compute the stop-boundary for ``get_project_dirs_up_to_home``'s upward walk.

    Normally the walk stops at the nearest ``.git`` above *cwd*.  However, if the
    Bash tool has cd'd into a nested git repo inside the session's project (submodule,
    vendored dep with its own ``.git``), stopping at that nested root makes the
    parent project's ``.hare/`` unreachable.

    The boundary is widened to the **session's** git root only when **both**:

      * the nearest ``.git`` from *cwd* belongs to a **different** canonical repo
        (submodule / vendored clone — not a worktree that resolves back to main)
      * that nearest ``.git`` sits **inside** the session's project tree

    TS:  resolveStopBoundary
    """
    from hare.bootstrap.state import get_project_root

    cwd_git_root = find_git_root(cwd)
    session_git_root = find_git_root(get_project_root())

    if not cwd_git_root or not session_git_root:
        return cwd_git_root

    # Resolve worktree `.git` files to the main repo.
    cwd_canonical = find_canonical_git_root(cwd)
    if (
        cwd_canonical
        and normalize_path_for_comparison(cwd_canonical)
        == normalize_path_for_comparison(session_git_root)
    ):
        # Same canonical repo (main, or a worktree of main). Stop at nearest .git.
        return cwd_git_root

    # Different canonical repo. Is it nested *inside* the session's project?
    n_cwd = normalize_path_for_comparison(cwd_git_root)
    n_sess = normalize_path_for_comparison(session_git_root)
    if n_cwd != n_sess and n_cwd.startswith(n_sess + os.sep):
        # Nested repo inside the project — skip past it, stop at project's root.
        return session_git_root

    # Sibling repo or elsewhere. Stop at nearest .git (old behaviour).
    return cwd_git_root


# =============================================================================
# Project-directory upward walk
# =============================================================================


def get_project_dirs_up_to_home(subdir: str, cwd: str) -> list[str]:
    """Traverse from *cwd* up to the git root (or home), collecting ``.hare/<subdir>``.

    Stopping at the git root prevents commands / skills from parent directories
    outside the repository from leaking into projects.  For example,
    ``~/projects/.hare/commands/`` won't appear in ``~/projects/my-repo/`` if
    *my-repo* is a git repository.

    The home directory itself is never checked — it is loaded separately as
    *userDir* (see ``load_markdown_files_for_subdir``).

    TS:  getProjectDirsUpToHome
    """
    home = normalize_path_for_comparison(str(Path.home().resolve()))
    git_root = resolve_stop_boundary(cwd)
    git_root_norm = normalize_path_for_comparison(git_root) if git_root else None
    current = os.path.normpath(os.path.abspath(cwd))
    dirs: list[str] = []

    for _ in range(64):
        # Stop at home (loaded separately as userDir)
        if normalize_path_for_comparison(current) == home:
            break

        claude_subdir = os.path.join(current, ".hare", subdir)

        # statSync + explicit error handling — re-throw unexpected errors.
        # Downstream loadMarkdownFiles handles the TOCTOU window (dir
        # disappearing before read) gracefully.
        try:
            os.stat(claude_subdir)
            dirs.append(claude_subdir)
        except OSError as e:
            if not is_fs_inaccessible(e):
                raise

        # Stop after processing the git-root directory — prevents commands from
        # parent directories outside the repository from appearing in the project.
        if (
            git_root_norm
            and normalize_path_for_comparison(current) == git_root_norm
        ):
            break

        # Move to parent
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent

    return dirs


# =============================================================================
# Native file search (no ripgrep)
# =============================================================================


async def find_markdown_files_native(
    dir_path: str,
    signal: asyncio.Event | None = None,
    timeout_s: float = 3.0,
) -> list[str]:
    """Native directory walker that finds ``*.md`` files.

    Exists alongside ripgrep for:
      1. ripgrep's poor startup performance in native builds
      2. fallback when ripgrep is unavailable
      3. explicit enable via ``CLAUDE_CODE_USE_NATIVE_FILE_SEARCH``

    Symlink handling:
      - Follows symlinks (equivalent to ripgrep's ``--follow`` flag)
      - Uses device + inode tracking to detect cycles
      - Falls back to ``os.path.realpath`` on systems without inode support

    Does **not** respect ``.gitignore`` (matches ripgrep with ``--no-ignore``).

    TS:  findMarkdownFilesNative
    """
    files: list[str] = []
    visited_dirs: set[str] = set()
    _deadline = asyncio.get_running_loop().time() + timeout_s

    async def _walk(current: str) -> None:
        nonlocal files, visited_dirs

        if signal and signal.is_set():
            return
        if asyncio.get_running_loop().time() > _deadline:
            return

        # Cycle detection via device + inode
        try:
            st = os.stat(current)
            if stat_module.S_ISDIR(st.st_mode):
                dir_key = f"{st.st_dev}:{st.st_ino}"
                if dir_key in visited_dirs:
                    log_for_debugging(
                        f"Skipping already visited directory (circular symlink): {current}"
                    )
                    return
                visited_dirs.add(dir_key)
        except OSError as e:
            log_for_debugging(f"Failed to stat directory {current}: {e}")
            return

        # Read entries
        try:
            entries = os.scandir(current)
        except OSError as e:
            log_for_debugging(f"Failed to read directory {current}: {e}")
            return

        for entry in entries:
            if signal and signal.is_set():
                break
            if asyncio.get_running_loop().time() > _deadline:
                break

            full_path = os.path.join(current, entry.name)

            try:
                if entry.is_symlink():
                    try:
                        st = os.stat(full_path)  # stat follows symlinks
                        if stat_module.S_ISDIR(st.st_mode):
                            await _walk(full_path)
                        elif stat_module.S_ISREG(st.st_mode) and entry.name.endswith(".md"):
                            files.append(os.path.normpath(full_path))
                    except OSError as e:
                        log_for_debugging(f"Failed to follow symlink {full_path}: {e}")
                elif entry.is_dir():
                    await _walk(full_path)
                elif entry.is_file() and entry.name.endswith(".md"):
                    files.append(os.path.normpath(full_path))
            except OSError as e:
                log_for_debugging(f"Failed to access {full_path}: {e}")

        # Close the scandir iterator
        try:
            entries.close()
        except Exception:
            pass

    await _walk(dir_path)
    return files


# =============================================================================
# Generic markdown-file loader (search + parse)
# =============================================================================


async def load_markdown_files(
    dir_path: str,
    *,
    use_native: bool = False,
) -> list[dict[str, Any]]:
    """Find and parse ``.md`` files under *dir_path*.

    Search strategy:
      - **Default**: ripgrep (faster, battle-tested)
      - **Fallback**: native fs walk (when ``CLAUDE_CODE_USE_NATIVE_FILE_SEARCH=1``
        or ripgrep is unavailable)

    Returns a list of ``{filePath, frontmatter, content}`` dicts.
    Missing / inaccessible directories return ``[]`` gracefully.

    TS:  loadMarkdownFiles
    """
    # --- File discovery ---
    use_native = use_native or is_env_truthy(
        os.environ.get("CLAUDE_CODE_USE_NATIVE_FILE_SEARCH")
    )

    files: list[str] = []

    if use_native:
        files = await find_markdown_files_native(dir_path)
    else:
        try:
            files = await rip_grep(
                ["--files", "--hidden", "--follow", "--no-ignore", "--glob", "*.md"],
                dir_path,
            )
        except OSError as e:
            if is_fs_inaccessible(e):
                return []
            raise

    # --- Parse each file ---
    results: list[dict[str, Any] | None] = []
    for file_path in files:
        try:
            raw = Path(file_path).read_text(encoding="utf-8")
            parsed = parse_frontmatter(raw, file_path)
            fm = parsed.get("frontmatter") or {}
            body = parsed.get("content") or ""
            results.append(
                {
                    "filePath": file_path,
                    "frontmatter": fm if isinstance(fm, dict) else {},
                    "content": body,
                }
            )
        except OSError as e:
            log_for_debugging(
                f"Failed to read/parse markdown file: {file_path}: {e}"
            )
            results.append(None)

    return [r for r in results if r is not None]


# =============================================================================
# Managed-path helper
# =============================================================================


def _get_managed_base_dir() -> str:
    """Return the managed-settings base directory under which ``.hare/<subdir>`` lives.

    TS:  ``join(getManagedFilePath(), '.claude', subdir)``
    We use ``get_managed_settings_drop_in_dir()`` (which returns
    ``~/.hare/settings.d``).  The convention mirrors the TS layout:
    ``<managed-base>/.hare/<subdir>``.
    """
    from hare.utils.settings.managed_path import get_managed_settings_drop_in_dir

    return get_managed_settings_drop_in_dir()


# =============================================================================
# Main entry-point: load markdown for subdir
# =============================================================================


@memoize_with_ttl_async
async def load_markdown_files_for_subdir(
    subdir: str,
    cwd: str,
) -> list[MarkdownFile]:
    """Load markdown files from managed, user, and project directories.

    Priority (highest to lowest, matching TS):  managed > user > project.

    Features:
      - Managed (policySettings) — always loaded
      - User (~/.hare/<subdir>) — gated by ``isSettingSourceEnabled('userSettings')``
      - Project (.hare/<subdir> up to git root) — gated by
        ``isSettingSourceEnabled('projectSettings')``
      - Plugin-only policy: when ``is_restricted_to_plugin_only('agents')`` is True,
        user and project agents are skipped
      - Git worktree fallback: when a worktree lacks ``.hare/<subdir>``, the main
        repository's copy is included
      - Inode-based deduplication: same physical file discovered through different
        paths (symlinks) appears only once

    Uses ``memoize_with_ttl_async`` keyed by ``(subdir, cwd)`` — 5-minute TTL with
    stale-while-revalidate background refresh.

    TS:  loadMarkdownFilesForSubdir
    """
    # Lazy imports to avoid circular deps at module level
    from hare.utils.settings.constants import is_setting_source_enabled
    from hare.utils.settings.plugin_only_policy import is_restricted_to_plugin_only

    user_dir = os.path.join(get_hare_config_home_dir(), subdir)
    managed_base = _get_managed_base_dir()
    managed_dir = os.path.join(managed_base, ".hare", subdir)
    project_dirs = get_project_dirs_up_to_home(subdir, cwd)

    # ── Worktree → main-repo fallback ────────────────────────────────────────
    # For git worktrees where the worktree does NOT have .hare/<subdir> checked
    # out (e.g. sparse-checkout), fall back to the main repository's copy.
    # get_project_dirs_up_to_home stops at the worktree root (where the .git file
    # is), so it never sees the main repo on its own.
    #
    # Only add the main repo's copy when the worktree root's .hare/<subdir>
    # is absent.  A standard `git worktree add` checks out the full tree, so the
    # worktree already has identical content — loading both would duplicate every
    # command / agent / skill.
    #
    # project_dirs already reflects existence (get_project_dirs_up_to_home checked
    # each dir), so we compare against that instead of stat'ing again.
    git_root = find_git_root(cwd)
    canonical_root = find_canonical_git_root(cwd)
    if git_root and canonical_root and canonical_root != git_root:
        worktree_subdir = normalize_path_for_comparison(
            os.path.join(git_root, ".hare", subdir)
        )
        worktree_has_subdir = any(
            normalize_path_for_comparison(d) == worktree_subdir
            for d in project_dirs
        )
        if not worktree_has_subdir:
            main_claude_subdir = os.path.join(canonical_root, ".hare", subdir)
            if main_claude_subdir not in project_dirs:
                project_dirs.append(main_claude_subdir)

    # ── Plugin-only gating ────────────────────────────────────────────────────
    plugin_only = subdir == "agents" and is_restricted_to_plugin_only("agents")

    # ── Load from all sources in parallel ──────────────────────────────────────
    managed_files_raw, user_files_raw, project_files_nested = await asyncio.gather(
        # Managed (policy) — always loaded
        _load_with_attribution(managed_dir, "policySettings"),
        # User — conditional
        _load_with_attribution(
            user_dir, "userSettings",
        )
        if (is_setting_source_enabled("userSettings") and not plugin_only)
        else _empty(),
        # Project — conditional (flattened after gather)
        _load_project_files(
            project_dirs,
        )
        if (is_setting_source_enabled("projectSettings") and not plugin_only)
        else _empty(),
    )

    # Flatten nested project-files array
    project_files: list[MarkdownFile] = []
    for group in project_files_nested:
        if isinstance(group, list):
            project_files.extend(group)

    # Combine with priority: managed > user > project
    all_files: list[MarkdownFile] = [
        *managed_files_raw,
        *user_files_raw,
        *project_files,
    ]

    # ── Inode-based deduplication ─────────────────────────────────────────────
    # Prevent the same file from appearing multiple times when ~/.hare is
    # symlinked to a directory within the project hierarchy.
    file_identities = await asyncio.gather(
        *(get_file_identity(f.file_path) for f in all_files)
    )

    seen_ids: dict[str, SettingSource] = {}
    deduplicated: list[MarkdownFile] = []

    for i, f in enumerate(all_files):
        fid = file_identities[i]
        if fid is None:
            # Cannot identify — include anyway (fail open)
            deduplicated.append(f)
            continue
        existing = seen_ids.get(fid)
        if existing is not None:
            log_for_debugging(
                f"Skipping duplicate file '{f.file_path}' from {f.source} "
                f"(same inode already loaded from {existing})"
            )
            continue
        seen_ids[fid] = f.source
        deduplicated.append(f)

    dupes = len(all_files) - len(deduplicated)
    if dupes > 0:
        log_for_debugging(
            f"Deduplicated {dupes} files in {subdir} "
            f"(same inode via symlinks or hard links)"
        )

    return deduplicated


# =============================================================================
# Internal helpers
# =============================================================================


async def _load_with_attribution(
    dir_path: str,
    source: SettingSource,
) -> list[MarkdownFile]:
    """Load markdown files from *dir_path*, stamping them with *source*.

    Returns ``[]`` when *dir_path* is missing or inaccessible.
    """
    # Early-exist check: if the directory doesn't exist, skip the expensive
    # ripgrep / native-walk entirely.
    if not os.path.isdir(dir_path):
        return []

    raw = await load_markdown_files(dir_path)
    return [
        MarkdownFile(
            file_path=f["filePath"],
            base_dir=dir_path,
            frontmatter=f["frontmatter"],
            content=f["content"],
            source=source,
        )
        for f in raw
    ]


async def _load_project_files(
    project_dirs: list[str],
) -> list[list[MarkdownFile]]:
    """Load markdown files from every project directory in parallel.

    Returns a list-of-lists (one per project directory) so the caller can flatten.
    """
    if not project_dirs:
        return []
    return await asyncio.gather(
        *(_load_with_attribution(d, "projectSettings") for d in project_dirs)
    )


async def _empty() -> list[MarkdownFile]:
    """Async no-op returning an empty list (used for conditional load paths)."""
    return []


# =============================================================================
# Cache management
# =============================================================================


def clear_markdown_cache() -> None:
    """Clear the memoization cache for ``load_markdown_files_for_subdir``.

    Useful for tests and state-reset (e.g. after config changes).
    TS:  loadMarkdownFilesForSubdir.cache.clear()
    """
    cache = getattr(load_markdown_files_for_subdir, "cache", None)
    if cache is None:
        return
    # memoize_with_ttl_async attaches a `.cache` object whose `.clear`
    # is a closure that doesn't accept `self`.  Try the method first,
    # fall back to calling the underlying `__func__`.
    clear_fn = getattr(cache, "clear", None)
    if clear_fn is None:
        return
    try:
        clear_fn()
    except TypeError:
        # Closure attached as method won't accept bound `self`; invoke
        # the raw function via its __func__ wrapper.
        raw = getattr(clear_fn, "__func__", clear_fn)
        raw()


# =============================================================================
# Description extraction
# =============================================================================


def extract_description_from_markdown(
    content: str, default_description: str = "Custom item"
) -> str:
    """Extract a human-readable description from markdown *content*.

    Uses the first non-empty line.  Strips leading ``#`` header markers.

    TS:  extractDescriptionFromMarkdown
    """
    import re

    for line in content.split("\n"):
        t = line.strip()
        if not t:
            continue
        hm = re.match(r"^#+\s+(.+)$", t)
        text = hm.group(1) if hm else t
        # TS: return text.length > 100 ? text.substring(0, 97) + '...' : text
        return text[:97] + "..." if len(text) > 100 else text
    return default_description


# =============================================================================
# Tool-list parsing from frontmatter
# =============================================================================


def _parse_tool_list_string(tools_value: Any) -> list[str] | None:
    """Parse tools from frontmatter — internal helper.

    Returns:
      * ``None``  — field is missing / absent (caller decides default)
      * ``[]``    — field is present but empty / falsy (no tools)
      * ``[...]`` — parsed tool list; ``['*']`` means all tools

    TS:  parseToolListString — passes a ``string[]`` to ``parseToolListFromCLI``.
    The Python ``parse_tool_list_from_cli`` is a stub that only accepts a
    comma-separated *string*, so we bridge by joining the array first.
    """
    if tools_value is None:
        return None
    if not tools_value:
        return []
    if isinstance(tools_value, str):
        arr = [tools_value]
    elif isinstance(tools_value, list):
        arr = [x for x in tools_value if isinstance(x, str)]
    else:
        return []

    if not arr:
        return []

    try:
        from hare.utils.permissions.permission_setup import parse_tool_list_from_cli  # type: ignore[import-not-found]

        # parse_tool_list_from_cli currently only accepts a comma-separated string.
        # Bridge the array → string gap so that the stub expands aliases / globs.
        joined = ",".join(arr)
        parsed = parse_tool_list_from_cli(joined)
    except ImportError:
        parsed = arr
    if "*" in parsed:
        return ["*"]
    return parsed


def parse_agent_tools_from_frontmatter(tools_value: Any) -> list[str] | None:
    """Parse tools from agent frontmatter.

    - Missing field → ``None`` (all tools)
    - Empty field   → ``[]``  (no tools)
    - ``*``         → ``None`` (all tools)

    TS:  parseAgentToolsFromFrontmatter
    """
    parsed = _parse_tool_list_string(tools_value)
    if parsed is None:
        return None if tools_value is None else []
    if "*" in parsed:
        return None
    return parsed


def parse_slash_command_tools_from_frontmatter(tools_value: Any) -> list[str]:
    """Parse allowed-tools from slash-command frontmatter.

    Missing or empty field → ``[]`` (no tools).

    TS:  parseSlashCommandToolsFromFrontmatter
    """
    parsed = _parse_tool_list_string(tools_value)
    if parsed is None:
        return []
    return parsed
