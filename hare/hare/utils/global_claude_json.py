"""Global Hare user JSON path and ``env`` — port of ``getGlobalClaudeFile`` + ``env``."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from hare.utils.env_utils import is_env_truthy

_global_json_cache: dict[str, Any] | None = None


def file_suffix_for_oauth_config() -> str:
    """Port of ``fileSuffixForOauthConfig()`` in ``constants/oauth.ts``."""
    if os.environ.get("CLAUDE_CODE_CUSTOM_OAUTH_URL"):
        return "-custom-oauth"
    if os.environ.get("USER_TYPE") == "ant":
        if is_env_truthy(os.environ.get("USE_LOCAL_OAUTH")):
            return "-local-oauth"
        if is_env_truthy(os.environ.get("USE_STAGING_OAUTH")):
            return "-staging-oauth"
    return ""


def get_global_hare_file_path() -> str:
    """Absolute path for the global Hare JSON config."""
    cfg_dir_raw = os.environ.get("HARE_CONFIG_DIR") or ""
    base = Path(cfg_dir_raw).expanduser() if cfg_dir_raw else Path.home()
    return str(base / f".hare{file_suffix_for_oauth_config()}.json")


def load_global_hare_json_uncached() -> dict[str, Any]:
    """Read global Hare JSON without raising (empty dict if missing/invalid)."""
    path = get_global_hare_file_path()
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        return raw if isinstance(raw, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def get_global_config_env() -> dict[str, str]:
    """Return ``GlobalConfig.env`` from the global Hare JSON — port for ``managedEnv``.

    Mirrors ``getGlobalConfig().env`` in ``utils/config.ts``.
    """
    global _global_json_cache
    if _global_json_cache is None:
        _global_json_cache = load_global_hare_json_uncached()
    env = _global_json_cache.get("env")
    if not isinstance(env, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in env.items():
        if isinstance(k, str):
            out[k] = "" if v is None else str(v)
    return out


def reset_global_hare_json_cache() -> None:
    """Invalidate cache (e.g. after tests mutate files)."""
    global _global_json_cache
    _global_json_cache = None


def reload_global_hare_json() -> dict[str, Any]:
    """Explicitly reload the global JSON snapshot from disk."""
    global _global_json_cache
    _global_json_cache = load_global_hare_json_uncached()
    return dict(_global_json_cache)
