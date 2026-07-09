"""Integration tests for session setup against real filesystem."""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# session_setup.setup
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_setup_basic_directory(tmp_path) -> None:
    """setup() against a plain temp dir should succeed and return known keys."""
    from hare.session_setup import setup

    result = await setup(cwd=str(tmp_path))
    assert isinstance(result, dict)
    assert "cwd" in result
    assert "project_root" in result
    assert "session_id" in result
    assert "permission_mode" in result


@pytest.mark.integration
@pytest.mark.asyncio
async def test_setup_respects_permission_mode(tmp_path) -> None:
    """setup() should record the requested permission_mode."""
    from hare.session_setup import setup

    result = await setup(cwd=str(tmp_path), permission_mode="bypassPermissions")
    assert result["permission_mode"] == "bypassPermissions"


@pytest.mark.integration
def test_bootstrap_state_session_id() -> None:
    """get_session_id() should return a non-empty string after import."""
    from hare.bootstrap.state import get_session_id

    sid = get_session_id()
    assert isinstance(sid, str)
    assert len(sid) > 0


@pytest.mark.integration
def test_global_config_loadable() -> None:
    """get_global_config() should return a GlobalConfig object without crashing."""
    from hare.utils.config import get_global_config

    cfg = get_global_config()
    assert cfg is not None
    assert hasattr(cfg, "theme")


# ---------------------------------------------------------------------------
# Git root detection (filesystem-only, no git execution)
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_find_git_root_non_git_dir(tmp_path) -> None:
    """find_git_root() in a non-git directory should return None."""
    from hare.utils.git import find_git_root

    result = await find_git_root(str(tmp_path))
    assert result is None
