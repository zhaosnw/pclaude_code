"""XDG Base Directory helpers (port of xdg.ts)."""

from __future__ import annotations

import os
from pathlib import Path


def _home(options: dict[str, str | None] | None, homedir: str | None) -> str:
    if homedir:
        return homedir
    return os.environ.get("HOME") or str(Path.home())


def get_xdg_state_home(
    *,
    env: dict[str, str | None] | None = None,
    homedir: str | None = None,
) -> str:
    e = env if env is not None else dict(os.environ)
    home = _home(env, homedir)
    return e.get("XDG_STATE_HOME") or str(Path(home) / ".local" / "state")


def get_xdg_cache_home(
    *,
    env: dict[str, str | None] | None = None,
    homedir: str | None = None,
) -> str:
    e = env if env is not None else dict(os.environ)
    home = _home(env, homedir)
    return e.get("XDG_CACHE_HOME") or str(Path(home) / ".cache")


def get_xdg_data_home(
    *,
    env: dict[str, str | None] | None = None,
    homedir: str | None = None,
) -> str:
    e = env if env is not None else dict(os.environ)
    home = _home(env, homedir)
    return e.get("XDG_DATA_HOME") or str(Path(home) / ".local" / "share")


def get_user_bin_dir(
    *,
    env: dict[str, str | None] | None = None,
    homedir: str | None = None,
) -> str:
    home = _home(env, homedir)
    return str(Path(home) / ".local" / "bin")
