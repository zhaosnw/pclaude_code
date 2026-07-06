"""Console / Hare.ai billing access helpers (port of billing.ts)."""

from __future__ import annotations

import os

from hare.utils.auth import get_api_key, is_hare_ai_subscriber
from hare.utils.env_utils import is_env_truthy


def has_console_billing_access() -> bool:
    if is_env_truthy(os.environ.get("DISABLE_COST_WARNINGS")):
        return False
    if is_hare_ai_subscriber():
        return False

    try:
        from hare.utils.auth import get_auth_token_source  # type: ignore[attr-defined]

        auth_source = get_auth_token_source()
        has_token = getattr(auth_source, "has_token", True)
    except ImportError:
        has_token = True

    if not has_token and get_api_key() is None:
        return False

    try:
        from hare.utils.config import get_global_config

        cfg = get_global_config()
        oauth = getattr(cfg, "oauth_account", None) or {}
        org_role = oauth.get("organizationRole")
        workspace_role = oauth.get("workspaceRole")
    except ImportError:
        return False

    if not org_role or not workspace_role:
        return False

    return org_role in ("admin", "billing") or workspace_role in (
        "workspace_admin",
        "workspace_billing",
    )


_mock_billing_access_override: bool | None = None


def set_mock_billing_access_override(value: bool | None) -> None:
    global _mock_billing_access_override
    _mock_billing_access_override = value


def has_hare_ai_billing_access() -> bool:
    if _mock_billing_access_override is not None:
        return _mock_billing_access_override
    if not is_hare_ai_subscriber():
        return False

    try:
        from hare.utils.auth import get_subscription_type

        st = get_subscription_type()
    except ImportError:
        st = None

    if st in ("max", "pro"):
        return True

    try:
        from hare.utils.config import get_global_config

        cfg = get_global_config()
        oauth = getattr(cfg, "oauth_account", None) or {}
        org_role = oauth.get("organizationRole")
    except ImportError:
        return False

    return bool(org_role and org_role in ("admin", "billing", "owner", "primary_owner"))
