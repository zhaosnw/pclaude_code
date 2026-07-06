"""
Marketplace reconciler — diff declared settings vs materialized JSON.

Port of: src/utils/plugins/reconciler.ts

Two layers:
- diff_marketplaces(): comparison (reads .git for worktree canonicalization)
- reconcile_marketplaces(): bundled diff + install (I/O, idempotent, additive)
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from hare.bootstrap.state import get_original_cwd
from hare.utils.debug import log_for_debugging
from hare.utils.errors import error_message
from hare.utils.git_utils import find_canonical_git_root
from hare.utils.log import log_error
from hare.utils.plugins.marketplace_manager import (
    add_marketplace_source,
    get_declared_marketplaces,
    load_known_marketplaces_config,
    load_known_marketplaces_config_safe,
)
from hare.utils.plugins.schemas import (
    is_local_marketplace_source,
)


# ---------------------------------------------------------------------------
# Deep equality (lodash isEqual equivalent)
# ---------------------------------------------------------------------------

def _deep_equal(a: Any, b: Any) -> bool:
    """Deep structural equality — replacement for lodash isEqual."""
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        if set(a.keys()) != set(b.keys()):
            return False
        return all(_deep_equal(a[k], b[k]) for k in a)
    if isinstance(a, list):
        if len(a) != len(b):
            return False
        return all(_deep_equal(x, y) for x, y in zip(a, b))
    if isinstance(a, (int, float)):
        return a == b
    return a == b


# ---------------------------------------------------------------------------
# Path utilities
# ---------------------------------------------------------------------------

async def _path_exists(path: str) -> bool:
    """Async equivalent of TS pathExists — check if a filesystem path exists."""
    try:
        return await asyncio.to_thread(os.path.exists, path)
    except (OSError, ValueError):
        return False


def _is_absolute(path: str) -> bool:
    """Check if a path is absolute (cross-platform)."""
    return os.path.isabs(path)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class MarketplaceDiff:
    """Diff results between declared and materialized marketplaces."""

    missing: list[str] = field(default_factory=list)
    source_changed: list[dict[str, Any]] = field(default_factory=list)
    up_to_date: list[str] = field(default_factory=list)


@dataclass
class ReconcileProgressEvent:
    """Progress event emitted during reconciliation."""

    type: Literal["installing", "installed", "failed"]
    name: str
    action: Literal["install", "update"] | None = None
    index: int | None = None
    total: int | None = None
    already_materialized: bool | None = None
    error: str | None = None


@dataclass
class ReconcileOptions:
    """Options controlling reconciliation behaviour."""

    skip: Callable[[str, Any], bool] | None = None
    on_progress: Callable[[dict[str, Any]], None] | None = None


@dataclass
class ReconcileResult:
    """Result of a full reconciliation pass."""

    installed: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    failed: list[dict[str, str]] = field(default_factory=list)
    up_to_date: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Source normalization
# ---------------------------------------------------------------------------


def normalize_source(
    source: Any,
    project_root: str | None = None,
) -> Any:
    """Resolve relative directory/file paths for stable comparison.

    Settings declared at project scope may use project-relative paths;
    JSON stores absolute paths.

    For git worktrees, resolve against the main checkout (canonical root)
    instead of the worktree cwd. Project settings are checked into git,
    so ``./foo`` means "relative to this repo" — but known_marketplaces.json
    is user-global with one entry per marketplace name. Resolving against
    the worktree cwd means each worktree session overwrites the shared entry
    with its own absolute path, and deleting the worktree leaves a dead
    installLocation. The canonical root is stable across all worktrees.
    """
    if not isinstance(source, dict):
        return source

    source_type = source.get("source")
    if source_type in ("directory", "file") and not _is_absolute(
        source.get("path", "")
    ):
        base = project_root or get_original_cwd()
        canonical_root = find_canonical_git_root(base)
        resolved_base = canonical_root if canonical_root else base
        resolved_path = os.path.abspath(
            os.path.join(resolved_base, source["path"])
        )
        return {**source, "path": resolved_path}

    return source


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------


def diff_marketplaces(
    declared: dict[str, Any],
    materialized: dict[str, Any],
    *,
    project_root: str | None = None,
) -> MarketplaceDiff:
    """Compare declared intent (settings) against materialized state (JSON).

    Resolves relative directory/file paths in ``declared`` before comparing,
    so project settings with ``./path`` match JSON's absolute path.
    """
    missing: list[str] = []
    source_changed: list[dict[str, Any]] = []
    up_to_date: list[str] = []

    for name, intent in declared.items():
        state = materialized.get(name)
        normalized_intent = normalize_source(intent.get("source"), project_root)

        if not state:
            # Declared in settings, absent from known_marketplaces.json
            missing.append(name)
            continue

        if intent.get("sourceIsFallback"):
            # Fallback: presence suffices. Don't compare sources — the declared
            # source is only a default for the `missing` branch. If a
            # seed/prior-install/mirror materialized this marketplace under ANY
            # source, leave it alone. Comparing would report sourceChanged →
            # re-clone → stomp the materialized content.
            up_to_date.append(name)
            continue

        if not _deep_equal(normalized_intent, state.get("source")):
            source_changed.append(
                {
                    "name": name,
                    "declaredSource": normalized_intent,
                    "materializedSource": state.get("source"),
                }
            )
        else:
            up_to_date.append(name)

    return MarketplaceDiff(
        missing=missing,
        source_changed=source_changed,
        up_to_date=up_to_date,
    )


# ---------------------------------------------------------------------------
# Reconcile
# ---------------------------------------------------------------------------


async def reconcile_marketplaces(
    *,
    skip: Callable[[str, Any], bool] | None = None,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
) -> ReconcileResult:
    """Make known_marketplaces.json consistent with declared intent.

    Idempotent. Additive only (never deletes). Does not touch AppState.

    Parameters
    ----------
    skip:
        Optional predicate. If True for a marketplace name+source, skip it.
        Used by zip-cache mode for unsupported source types.
    on_progress:
        Optional callback receiving progress events as dicts with keys:
        ``type`` (installing/installed/failed), ``name``, ``action``
        (install/update, installing only), ``index``, ``total``
        (installing only), ``alreadyMaterialized`` (installed only),
        ``error`` (failed only).
    """
    declared = get_declared_marketplaces()
    if not declared:
        return ReconcileResult()

    # Load materialized state safely — never throw on corrupt JSON
    materialized: dict[str, Any] = {}
    try:
        materialized = await load_known_marketplaces_config()
    except Exception as e:
        log_error(e)
        try:
            materialized = await load_known_marketplaces_config_safe()
        except Exception:
            materialized = {}

    diff = diff_marketplaces(declared, materialized, project_root=get_original_cwd())

    # Build work items from diff
    work_items: list[dict[str, Any]] = []

    for name in diff.missing:
        intent = declared.get(name, {})
        work_items.append(
            {
                "name": name,
                "source": normalize_source(intent.get("source")),
                "action": "install",
            }
        )

    for entry in diff.source_changed:
        work_items.append(
            {
                "name": entry["name"],
                "source": entry["declaredSource"],
                "action": "update",
            }
        )

    # Apply skip filter and guard dead local paths
    skipped: list[str] = []
    to_process: list[dict[str, Any]] = []

    for item in work_items:
        name = item["name"]
        source = item["source"]
        action = item["action"]

        if skip is not None and skip(name, source):
            skipped.append(name)
            continue

        # For sourceChanged local-path entries, skip if the declared path
        # doesn't exist. Guards multi-checkout scenarios where normalizeSource
        # can't canonicalize and produces a dead path — the materialized entry
        # may still be valid; addMarketplaceSource would fail anyway, so
        # skipping avoids a noisy "failed" event and preserves the working
        # entry. Missing entries are NOT skipped (nothing to preserve; the
        # user should see the error).
        if (
            action == "update"
            and is_local_marketplace_source(source)
            and not await _path_exists(source.get("path", ""))
        ):
            log_for_debugging(
                f"[reconcile] '{name}' declared path does not exist; "
                f"keeping materialized entry",
            )
            skipped.append(name)
            continue

        to_process.append(item)

    # Early exit: nothing to do
    if not to_process:
        return ReconcileResult(
            up_to_date=diff.up_to_date,
            skipped=skipped,
        )

    log_for_debugging(
        f"[reconcile] {len(to_process)} marketplace(s): "
        f"{', '.join(f'{w['name']}({w['action']})' for w in to_process)}"
    )

    installed: list[str] = []
    updated: list[str] = []
    failed: list[dict[str, str]] = []

    for i, item in enumerate(to_process):
        name = item["name"]
        source = item["source"]
        action = item["action"]

        # Fire progress callback: installing
        if on_progress is not None:
            _safe_call_progress(
                on_progress,
                {
                    "type": "installing",
                    "name": name,
                    "action": action,
                    "index": i + 1,
                    "total": len(to_process),
                },
            )

        try:
            # addMarketplaceSource is source-idempotent — same source returns
            # alreadyMaterialized:true without cloning. For 'update' (source
            # changed), the new source won't match existing → proceeds with
            # clone and overwrites the old JSON entry.
            result = await add_marketplace_source(source)

            if action == "install":
                installed.append(name)
            else:
                updated.append(name)

            if on_progress is not None:
                _safe_call_progress(
                    on_progress,
                    {
                        "type": "installed",
                        "name": name,
                        "alreadyMaterialized": result.get(
                            "alreadyMaterialized", False
                        ),
                    },
                )
        except Exception as e:
            err_msg = error_message(e)
            failed.append({"name": name, "error": err_msg})

            if on_progress is not None:
                _safe_call_progress(
                    on_progress,
                    {"type": "failed", "name": name, "error": err_msg},
                )

            log_error(e)

    return ReconcileResult(
        installed=installed,
        updated=updated,
        failed=failed,
        up_to_date=diff.up_to_date,
        skipped=skipped,
    )


# ---------------------------------------------------------------------------
# Progress callback safety
# ---------------------------------------------------------------------------


def _safe_call_progress(
    callback: Callable[[dict[str, Any]], None],
    event: dict[str, Any],
) -> None:
    """Invoke a progress callback, catching and logging any errors."""
    try:
        callback(event)
    except Exception as exc:
        log_for_debugging(
            f"Progress callback error: {error_message(exc)}",
            level="warn",
        )
