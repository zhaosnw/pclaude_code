"""
Environment probes that need subprocess or native feature flags.

Port of: src/utils/envDynamic.ts

This module provides dynamic environment detection that requires subprocess
calls or filesystem access, complementing the static env.py module. Key
behaviours:

- Docker detection via /.dockerenv (memoized, Linux-only)
- Bubblewrap sandbox detection via env var
- MUSL libc detection (compile-time flags + runtime fallback)
- JetBrains IDE terminal detection via parent-process inspection
- Merged ``env_dynamic`` namespace that overrides ``terminal`` with
  JetBrains-aware detection while delegating everything else to ``env``.
"""

from __future__ import annotations

import os
import platform
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from hare.utils.env import JETBRAINS_IDES, env, get_host_platform_for_analytics
from hare.utils.env_utils import is_env_truthy
from hare.utils.exec_file_no_throw import exec_file_no_throw

if TYPE_CHECKING:
    from typing import Any

# ---------------------------------------------------------------------------
# Feature-flag stub (mirrors TS `feature()` from bun:bundle)
# ---------------------------------------------------------------------------


def _feature(name: str) -> bool:
    """Native build feature flags; stub via env ``FEATURE_<NAME>``.

    In a native (compiled) build these flags are resolved at compile time.
    For unbundled Python / Node they fall back to environment variables.
    """
    return os.environ.get(f"FEATURE_{name}") == "1"


# ===================================================================
# MUSL libc runtime detection (Linux only)
# ===================================================================

_musl_runtime_cache: bool | None = None
_musl_cache_lock = threading.Lock()


def _resolve_musl_cache() -> None:
    """Populate ``_musl_runtime_cache`` with a synchronous filesystem check.

    Called from a fire-and-forget daemon thread at module-load time on
    Linux so we never block startup.  Native builds short-circuit via
    compile-time feature flags and never reach this path.
    """
    global _musl_runtime_cache
    try:
        arch = platform.machine()
        musl_arch = "x86_64" if arch in ("x86_64", "AMD64") else "aarch64"
        p = Path(f"/lib/libc.musl-{musl_arch}.so.1")
        with _musl_cache_lock:
            _musl_runtime_cache = p.is_file()
    except Exception:
        # Any failure (permissions, missing /lib, …) → assume glibc.
        with _musl_cache_lock:
            _musl_runtime_cache = False


def _prime_musl_cache() -> None:
    """Fire-and-forget population of the musl runtime cache.

    Only meaningful on Linux where both native feature flags are absent
    (i.e. unbundled Node / Python).  Runs in a daemon thread so that
    ``is_musl_environment()`` can return ``False`` until the cache is
    populated — matching the TS ``muslRuntimeCache ?? false`` fallback.
    """
    if platform.system() != "Linux":
        return
    # Skip if the feature flags already tell us the answer at compile time.
    if _feature("IS_LIBC_MUSL") or _feature("IS_LIBC_GLIBC"):
        return
    threading.Thread(target=_resolve_musl_cache, daemon=True).start()


if platform.system() == "Linux":
    _prime_musl_cache()


def is_musl_environment() -> bool:
    """Return ``True`` when the host libc is MUSL (not glibc).

    Resolution order (matches TS):
    1. Compile-time ``IS_LIBC_MUSL`` → ``True``
    2. Compile-time ``IS_LIBC_GLIBC`` → ``False``
    3. Linux runtime cache (populated async at module load) → ``bool(cache)``
    4. Non-Linux → ``False``
    """
    if _feature("IS_LIBC_MUSL"):
        return True
    if _feature("IS_LIBC_GLIBC"):
        return False
    if platform.system() != "Linux":
        return False
    with _musl_cache_lock:
        return bool(_musl_runtime_cache)


# ===================================================================
# Docker / sandbox detection
# ===================================================================

_DOCKER_SENTINEL = object()
_docker_cache: bool | object = _DOCKER_SENTINEL
_docker_cache_lock = threading.Lock()


async def get_is_docker() -> bool:
    """Return ``True`` when running inside a Docker container (Linux only).

    Checks for the ``/.dockerenv`` sentinel file via ``test -f``.
    The result is **memoized** — subsequent calls return the cached value
    without spawning a subprocess (matching the TS ``memoize`` wrapper).
    """
    global _docker_cache

    # Fast-path: cache already populated.
    if _docker_cache is not _DOCKER_SENTINEL:
        return bool(_docker_cache)

    if platform.system() != "Linux":
        with _docker_cache_lock:
            _docker_cache = False
        return False

    try:
        r = await exec_file_no_throw("test", ["-f", "/.dockerenv"])
        result = r["code"] == 0
    except Exception:
        # If anything goes wrong (e.g. subprocess fork failure), assume
        # not Docker rather than crashing.
        result = False

    with _docker_cache_lock:
        _docker_cache = result
    return result


def get_is_docker_sync() -> bool | None:
    """Synchronous peek at the Docker cache.

    Returns the cached value if it has been populated (by a prior
    ``await get_is_docker()`` call), or ``None`` if the cache is
    still uninitialised.
    """
    if _docker_cache is _DOCKER_SENTINEL:
        return None
    return bool(_docker_cache)


def get_is_bubblewrap_sandbox() -> bool:
    """Return ``True`` when the CLAUDE_CODE_BUBBLEWRAP env var is truthy.

    Only meaningful on Linux (bubblewrap is Linux-only).
    """
    return platform.system() == "Linux" and is_env_truthy(
        os.environ.get("CLAUDE_CODE_BUBBLEWRAP")
    )


# ===================================================================
# JetBrains IDE terminal detection
# ===================================================================

_UNSET = object()
_jetbrains_ide_cache: str | None | object = _UNSET
_jetbrains_cache_lock = threading.Lock()


async def _detect_jetbrains_ide_from_parent_process_async() -> str | None:
    """Walk the process tree looking for a known JetBrains IDE command.

    The result is cached globally after the first call.  On macOS we
    skip parent-process inspection because bundle-ID detection (done in
    ``env.detect_terminal()``) already handles JetBrains IDEs.

    Returns the IDE slug (e.g. ``"pycharm"``) or ``None``.
    """
    global _jetbrains_ide_cache

    # Serve from cache if already resolved.
    if _jetbrains_ide_cache is not _UNSET:
        return None if _jetbrains_ide_cache is None else str(_jetbrains_ide_cache)

    # On macOS, JetBrains IDEs are detected via bundle ID in env.py.
    if platform.system() == "Darwin":
        with _jetbrains_cache_lock:
            _jetbrains_ide_cache = None
        return None

    try:
        from hare.utils.generic_process_utils import get_ancestor_commands_async
    except ImportError:
        # If the import fails (e.g. in a minimal environment), treat as
        # undetected rather than crashing.
        with _jetbrains_cache_lock:
            _jetbrains_ide_cache = None
        return None

    try:
        commands = await get_ancestor_commands_async(os.getpid(), 10)
        for command in commands:
            lower = command.lower()
            for ide in JETBRAINS_IDES:
                if ide in lower:
                    with _jetbrains_cache_lock:
                        _jetbrains_ide_cache = ide
                    return ide
    except Exception:
        # Best-effort detection — silently swallow any failure (subprocess
        # errors, permission issues, process-tree changes, …).
        pass

    with _jetbrains_cache_lock:
        _jetbrains_ide_cache = None
    return None


async def get_terminal_with_jetbrains_detection_async() -> str | None:
    """Async terminal detection with JetBrains-specific heuristics.

    When ``TERMINAL_EMULATOR=JetBrains-JediTerm`` and the platform is
    **not** macOS, we walk the parent process tree to identify the
    *specific* JetBrains IDE (e.g. ``"intellij"`` vs ``"webstorm"``).
    Falls back to ``"pycharm"`` if detection fails.

    On all other terminals / platforms, delegates to ``env.terminal``.
    """
    if os.environ.get("TERMINAL_EMULATOR") == "JetBrains-JediTerm":
        if env.platform != "darwin":
            specific = await _detect_jetbrains_ide_from_parent_process_async()
            return specific or "pycharm"
    return env.terminal


def get_terminal_with_jetbrains_detection() -> str | None:
    """Synchronous terminal detection with JetBrains heuristics.

    Uses the cached JetBrains IDE result if it has already been
    populated by an earlier call to the async detection pathway.
    Otherwise falls back to a generic ``"pycharm"`` label for
    JetBrains terminals, or delegates to ``env.terminal``.

    Callers that need accurate detection should ensure
    ``init_jetbrains_detection()`` has been awaited first.
    """
    if os.environ.get("TERMINAL_EMULATOR") == "JetBrains-JediTerm":
        if env.platform != "darwin":
            if _jetbrains_ide_cache is not _UNSET:
                # Cache populated — return the detected IDE or "pycharm"
                # as the generic fallback (matching TS behaviour).
                return _jetbrains_ide_cache if _jetbrains_ide_cache else "pycharm"
            # Cache not yet populated — return generic fallback.
            return "pycharm"
    return env.terminal


async def init_jetbrains_detection() -> None:
    """Pre-warm the JetBrains IDE cache.

    Call this early in application initialisation so that later
    synchronous calls to ``get_terminal_with_jetbrains_detection()``
    return accurate results without blocking on process-tree inspection.
    """
    if os.environ.get("TERMINAL_EMULATOR") == "JetBrains-JediTerm":
        await _detect_jetbrains_ide_from_parent_process_async()


def get_jetbrains_ide_cache() -> str | None:
    """Return the cached JetBrains IDE slug, or ``None``.

    Exposed for diagnostic / debugging purposes.  Returns ``None``
    when the cache has not been populated yet or detection found no
    matching IDE.
    """
    if _jetbrains_ide_cache is _UNSET:
        return None
    return _jetbrains_ide_cache if _jetbrains_ide_cache else None


# ===================================================================
# Combined namespace (mirrors TS `envDynamic`)
# ===================================================================


class EnvDynamicNamespace:
    """Aggregate namespace that merges ``env`` properties with dynamic
    overrides.

    The ``terminal`` attribute is replaced with the JetBrains-aware
    synchronous detector so that every consumer picks up the enhanced
    detection without code changes.

    All other attributes (``is_ci``, ``platform``, ``arch``, …) are
    delegated directly from ``env``.
    """

    # --- delegated from env -------------------------------------------------
    has_internet_access = env.has_internet_access
    is_ci = env.is_ci
    platform = env.platform
    arch = env.arch
    node_version = env.node_version
    is_ssh = env.is_ssh
    get_package_managers = env.get_package_managers
    get_runtimes = env.get_runtimes
    is_running_with_bun = env.is_running_with_bun
    is_wsl_environment = env.is_wsl_environment
    is_npm_from_windows_path = env.is_npm_from_windows_path
    is_conductor = env.is_conductor
    detect_deployment_environment = env.detect_deployment_environment
    get_host_platform_for_analytics = staticmethod(get_host_platform_for_analytics)

    # --- overrides ----------------------------------------------------------
    terminal = get_terminal_with_jetbrains_detection()

    # --- dynamic functions (not on env) -------------------------------------
    get_is_docker = staticmethod(get_is_docker)
    get_is_docker_sync = staticmethod(get_is_docker_sync)
    get_is_bubblewrap_sandbox = staticmethod(get_is_bubblewrap_sandbox)
    is_musl_environment = staticmethod(is_musl_environment)
    get_terminal_with_jetbrains_detection_async = staticmethod(
        get_terminal_with_jetbrains_detection_async
    )
    init_jetbrains_detection = staticmethod(init_jetbrains_detection)
    get_jetbrains_ide_cache = staticmethod(get_jetbrains_ide_cache)


env_dynamic = EnvDynamicNamespace()


# ===================================================================
# Cache reset helpers (useful for testing)
# ===================================================================


def _reset_docker_cache() -> None:
    """Reset the Docker detection cache (test-only)."""
    global _docker_cache
    with _docker_cache_lock:
        _docker_cache = _DOCKER_SENTINEL


def _reset_jetbrains_cache() -> None:
    """Reset the JetBrains IDE detection cache (test-only)."""
    global _jetbrains_ide_cache
    with _jetbrains_cache_lock:
        _jetbrains_ide_cache = _UNSET


def _reset_musl_cache() -> None:
    """Reset the MUSL runtime cache and re-prime it (test-only)."""
    global _musl_runtime_cache
    with _musl_cache_lock:
        _musl_runtime_cache = None
    if platform.system() == "Linux":
        _prime_musl_cache()


__all__ = [
    "env_dynamic",
    "get_is_docker",
    "get_is_docker_sync",
    "get_is_bubblewrap_sandbox",
    "is_musl_environment",
    "get_terminal_with_jetbrains_detection_async",
    "get_terminal_with_jetbrains_detection",
    "init_jetbrains_detection",
    "get_jetbrains_ide_cache",
    "_reset_docker_cache",
    "_reset_jetbrains_cache",
    "_reset_musl_cache",
]
