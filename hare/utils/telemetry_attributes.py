"""OpenTelemetry metric attributes (port of telemetryAttributes.ts)."""

from __future__ import annotations

import os
import uuid
from typing import Any

from hare.bootstrap import state as bootstrap_state
from hare.utils.env_dynamic import env_dynamic
from hare.utils.env_utils import is_env_truthy
from hare.utils.tagged_id import to_tagged_id

_METRICS_DEFAULTS = {
    "OTEL_METRICS_INCLUDE_SESSION_ID": True,
    "OTEL_METRICS_INCLUDE_VERSION": False,
    "OTEL_METRICS_INCLUDE_ACCOUNT_UUID": True,
}

_VERSION = "2.1.88"


def _should_include(key: str) -> bool:
    default = _METRICS_DEFAULTS.get(key, False)
    env_val = os.environ.get(key)
    if env_val is None:
        return default
    return is_env_truthy(env_val)


def get_telemetry_attributes() -> dict[str, Any]:
    user_id = str(uuid.uuid4())
    session_id = bootstrap_state.get_session_id()
    attrs: dict[str, Any] = {"user.id": user_id}

    if _should_include("OTEL_METRICS_INCLUDE_SESSION_ID"):
        attrs["session.id"] = session_id
    if _should_include("OTEL_METRICS_INCLUDE_VERSION"):
        attrs["app.version"] = _VERSION

    try:
        from hare.utils.auth import get_oauth_account_info

        oauth = get_oauth_account_info()
    except (ImportError, AttributeError):
        oauth = None

    if oauth:
        if oauth.get("organizationUuid"):
            attrs["organization.id"] = oauth["organizationUuid"]
        if oauth.get("emailAddress"):
            attrs["user.email"] = oauth["emailAddress"]
        account_uuid = oauth.get("accountUuid")
        if account_uuid and _should_include("OTEL_METRICS_INCLUDE_ACCOUNT_UUID"):
            attrs["user.account_uuid"] = account_uuid
            attrs["user.account_id"] = os.environ.get(
                "CLAUDE_CODE_ACCOUNT_TAGGED_ID"
            ) or to_tagged_id("user", account_uuid)

    term = getattr(env_dynamic, "terminal", None)
    if term:
        attrs["terminal.type"] = term

    return attrs
