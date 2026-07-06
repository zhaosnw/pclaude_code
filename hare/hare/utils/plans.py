"""Port of: src/utils/plans.ts

Plan file management — slug generation, lazy plan file paths, resume support.
"""

from __future__ import annotations

import os
from typing import Any

from hare.utils.env_utils import get_hare_config_home_dir

# Per-session slug cache: session_id -> slug string
_slug_cache: dict[str, str] = {}
_plans_dir: str | None = None


def _generate_word_slug() -> str:
    """Generate a random readable word slug."""
    import uuid

    # Use first 8 chars of a UUID as slug
    return uuid.uuid4().hex[:8]


def get_plans_directory() -> str:
    """Get or create the plans directory. Memoized per session."""
    global _plans_dir
    if _plans_dir is not None:
        return _plans_dir

    from hare.utils.cwd import get_cwd

    plans_path = os.path.join(get_hare_config_home_dir(), "plans")

    # Check settings for custom plans dir
    try:
        from hare.utils.settings.settings import get_initial_settings

        settings = get_initial_settings()
        settings_dir = settings.get("plansDirectory", "")
        if settings_dir:
            cwd = get_cwd()
            resolved = os.path.join(cwd, settings_dir)
            resolved = os.path.normpath(resolved)
            # Validate: must be within project root
            if resolved.startswith(os.path.normpath(cwd)):
                plans_path = resolved
    except Exception:
        pass

    os.makedirs(plans_path, exist_ok=True)
    _plans_dir = plans_path
    return plans_path


def get_plan_slug(session_id: str | None = None) -> str:
    """Get or generate a word slug for the current session's plan."""
    from hare.bootstrap.state import get_session_id

    sid = session_id or get_session_id() or "default"

    if sid not in _slug_cache:
        plans_dir = get_plans_directory()
        for _ in range(10):
            slug = _generate_word_slug()
            file_path = os.path.join(plans_dir, f"{slug}.md")
            if not os.path.exists(file_path):
                _slug_cache[sid] = slug
                break
        else:
            slug = _generate_word_slug()
            _slug_cache[sid] = slug

    return _slug_cache[sid]


def set_plan_slug(session_id: str, slug: str) -> None:
    """Set a specific plan slug for a session (used when resuming)."""
    _slug_cache[session_id] = slug


def clear_plan_slug(session_id: str | None = None) -> None:
    """Clear the plan slug for the current session."""
    from hare.bootstrap.state import get_session_id

    sid = session_id or get_session_id() or "default"
    _slug_cache.pop(sid, None)


def clear_all_plan_slugs() -> None:
    """Clear ALL plan slug entries."""
    _slug_cache.clear()


def get_plan_file_path(agent_id: str | None = None) -> str:
    """Get the file path for a session's plan file.

    For main conversation: {planSlug}.md
    For subagents: {planSlug}-agent-{agentId}.md
    """
    slug = get_plan_slug()
    plans_dir = get_plans_directory()
    if agent_id:
        return os.path.join(plans_dir, f"{slug}-agent-{agent_id}.md")
    return os.path.join(plans_dir, f"{slug}.md")


def get_plan(agent_id: str | None = None) -> str | None:
    """Get the plan content for a session."""
    file_path = get_plan_file_path(agent_id)
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return None
    except OSError as e:
        from hare.utils.log import log_error

        log_error(e)
        return None


def save_plan(content: str, agent_id: str | None = None) -> str:
    """Write plan content and return the file path."""
    file_path = get_plan_file_path(agent_id)
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)
    return file_path


def copy_plan_for_resume(
    log: dict[str, Any],
    target_session_id: str | None = None,
) -> bool:
    """Restore plan slug from a resumed session.

    Extracts the slug from the log's message history, sets the slug
    in the session cache, and verifies the plan file exists.
    Returns True if a plan file exists for the slug.
    """
    # Extract slug from log messages
    slug = _get_slug_from_log(log)
    if not slug:
        return False

    from hare.bootstrap.state import get_session_id

    session_id = target_session_id or get_session_id()
    if not session_id:
        return False

    set_plan_slug(session_id, slug)

    plan_path = os.path.join(get_plans_directory(), f"{slug}.md")
    if os.path.isfile(plan_path):
        return True

    # Plan file missing — try to recover from file snapshots
    messages = log.get("messages") or []
    for m in messages:
        file_snapshots = log.get("fileHistorySnapshots") or []
        for snap in file_snapshots:
            snap_data = snap.get("snapshot", snap)
            if isinstance(snap_data, dict) and snap_data.get("planContent"):
                try:
                    os.makedirs(os.path.dirname(plan_path), exist_ok=True)
                    with open(plan_path, "w", encoding="utf-8") as f:
                        f.write(snap_data["planContent"])
                    return True
                except OSError:
                    pass

    return False


def _get_slug_from_log(log: dict[str, Any]) -> str | None:
    """Extract plan slug from a log's message history."""
    messages = log.get("messages") or []
    for m in messages:
        if isinstance(m, dict) and m.get("slug"):
            return m["slug"]
    # Also check the log metadata
    slug = log.get("slug") or log.get("planSlug")
    if isinstance(slug, str) and slug:
        return slug
    return None
