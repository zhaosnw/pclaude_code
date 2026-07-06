"""Apply settings.env to process environment — port of `managedEnv.ts`."""

from __future__ import annotations

import os
from typing import Any

from hare.utils.env_utils import is_env_truthy
from hare.utils.global_claude_json import get_global_config_env
from hare.utils.managed_env_constants import SAFE_ENV_VARS, is_provider_managed_env_var
from hare.utils.settings.constants import is_setting_source_enabled
from hare.utils.settings.settings import (
    get_settings_deprecated,
    get_settings_for_source,
)

_ccd_spawn_keys: set[str] | None | bool = False


def _coerce_env(env: Any) -> dict[str, str]:
    if not isinstance(env, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in env.items():
        if not isinstance(k, str):
            continue
        out[k] = "" if v is None else str(v)
    return out


def _without_ssh_tunnel_vars(env: dict[str, str] | None) -> dict[str, str]:
    if not env or not os.environ.get("ANTHROPIC_UNIX_SOCKET"):
        return dict(env or {})
    drop = {
        "ANTHROPIC_UNIX_SOCKET",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "CLAUDE_CODE_OAUTH_TOKEN",
    }
    return {k: v for k, v in env.items() if k not in drop}


def _without_host_managed_provider_vars(env: dict[str, str] | None) -> dict[str, str]:
    if not env:
        return {}
    if not is_env_truthy(os.environ.get("CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST")):
        return dict(env)
    return {k: v for k, v in env.items() if not is_provider_managed_env_var(k)}


def _without_ccd_spawn_keys(env: dict[str, str] | None) -> dict[str, str]:
    global _ccd_spawn_keys
    if not env or not isinstance(_ccd_spawn_keys, set):
        return dict(env or {})
    return {k: v for k, v in env.items() if k not in _ccd_spawn_keys}


def _filter_settings_env(env: dict[str, str] | None) -> dict[str, str]:
    return _without_ccd_spawn_keys(
        _without_host_managed_provider_vars(_without_ssh_tunnel_vars(env))
    )


def _merge_env_into_process(env: dict[str, str]) -> None:
    for k, v in env.items():
        os.environ[k] = v


def apply_safe_config_environment_variables(project_dir: str | None = None) -> None:
    """Port of ``applySafeConfigEnvironmentVariables()`` (`managedEnv.ts`)."""

    global _ccd_spawn_keys
    if _ccd_spawn_keys is False:
        _ccd_spawn_keys = (
            set(os.environ.keys())
            if os.environ.get("CLAUDE_CODE_ENTRYPOINT") == "hare-desktop"
            else None
        )

    from hare.utils.cwd import get_cwd as _gcwd

    cwd = project_dir if project_dir is not None else _gcwd()

    _merge_env_into_process(_filter_settings_env(dict(get_global_config_env())))

    for source in ("userSettings", "flagSettings"):
        if not is_setting_source_enabled(source):
            continue
        raw = get_settings_for_source(source, project_dir=cwd)
        part = _coerce_env((raw or {}).get("env"))
        _merge_env_into_process(_filter_settings_env(part))

    try:
        from hare.services.remote_managed_settings.sync_cache import (
            is_remote_managed_settings_eligible,
        )

        is_remote_managed_settings_eligible()
    except ImportError:
        pass

    pol = get_settings_for_source("policySettings", project_dir=cwd)
    _merge_env_into_process(_filter_settings_env(_coerce_env((pol or {}).get("env"))))

    merged = get_settings_deprecated(project_dir=cwd)
    merged_env = _filter_settings_env(_coerce_env(merged.get("env")))
    for k, v in merged_env.items():
        if k.upper() in SAFE_ENV_VARS:
            os.environ[k] = v


def apply_config_environment_variables(project_dir: str | None = None) -> None:
    """Port of ``applyConfigEnvironmentVariables()`` — full merged env after trust."""

    from hare.utils.cwd import get_cwd as _gcwd

    cwd = project_dir if project_dir is not None else _gcwd()

    _merge_env_into_process(_filter_settings_env(dict(get_global_config_env())))

    merged = get_settings_deprecated(project_dir=cwd)
    _merge_env_into_process(_filter_settings_env(_coerce_env(merged.get("env"))))

    try:
        from hare.utils.ca_certs import clear_ca_certs_cache
        from hare.utils.mtls import clear_mtls_cache
        from hare.utils.proxy import clear_proxy_cache, configure_global_agents

        clear_ca_certs_cache()
        clear_mtls_cache()
        clear_proxy_cache()
        configure_global_agents()
    except ImportError:
        pass
