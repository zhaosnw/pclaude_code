"""Populate `NODE_EXTRA_CA_CERTS` from user settings (`caCertsConfig.ts`)."""

from __future__ import annotations

import os

from hare.utils.debug import log_for_debugging


def _get_extra_certs_path_from_config() -> str | None:
    try:
        from hare.utils.global_claude_json import get_global_config_env
        from hare.utils.settings.settings import get_settings_for_source

        settings = get_settings_for_source("userSettings")
        settings_env = (
            (settings or {}).get("env") if isinstance(settings, dict) else None
        )

        global_env = get_global_config_env()

        path: str | None = None
        if isinstance(settings_env, dict):
            v = settings_env.get("NODE_EXTRA_CA_CERTS")
            if v is not None:
                path = str(v)
        if path is None and global_env.get("NODE_EXTRA_CA_CERTS"):
            path = str(global_env["NODE_EXTRA_CA_CERTS"])
        return path or None
    except Exception:
        return None


def apply_extra_ca_certs_from_config() -> None:
    if os.environ.get("NODE_EXTRA_CA_CERTS"):
        return
    config_path = _get_extra_certs_path_from_config()
    if config_path:
        os.environ["NODE_EXTRA_CA_CERTS"] = config_path
        log_for_debugging(
            f"CA certs: Applied NODE_EXTRA_CA_CERTS from config to process.env: {config_path}",
        )
