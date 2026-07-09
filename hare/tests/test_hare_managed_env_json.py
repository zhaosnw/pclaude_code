"""Integration tests for Hare managed env + global Hare JSON."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from hare.utils.global_claude_json import (
    get_global_hare_file_path,
    reset_global_hare_json_cache,
)
from hare.utils.managed_env import apply_safe_config_environment_variables

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _reset_global_json_cache_each() -> None:
    reset_global_hare_json_cache()
    yield
    reset_global_hare_json_cache()


def test_global_hare_file_uses_hare_json(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("HARE_CONFIG_DIR", raising=False)

    hare_cfg = tmp_path / ".hare.json"
    hare_cfg.write_text("{}")

    assert Path(get_global_hare_file_path()).resolve() == hare_cfg.resolve()


def test_safe_env_applies_global_json_and_user_settings(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("HARE_CONFIG_DIR", raising=False)

    reset_global_hare_json_cache()

    (tmp_path / ".hare.json").write_text(json.dumps({"env": {"EGLOBAL": "gv"}}))

    hare_home = tmp_path / ".hare"
    hare_home.mkdir(parents=True, exist_ok=True)
    (hare_home / "settings.json").write_text(json.dumps({"env": {"EUSER": "uv"}}))

    proj = tmp_path / "proj"
    proj.mkdir()

    from hare.bootstrap.state import set_cwd

    monkeypatch.chdir(proj)
    set_cwd(str(proj))

    for key in ("EGLOBAL", "EUSER"):
        os.environ.pop(key, None)

    apply_safe_config_environment_variables(project_dir=str(proj))

    assert os.environ.get("EGLOBAL") == "gv"
    assert os.environ.get("EUSER") == "uv"
