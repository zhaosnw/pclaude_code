"""
REPL-specific bridge bootstrap — gate checks, OAuth, session creation, title derivation.

Port of: src/bridge/initReplBridge.ts

Orchestrates the full bridge initialization:
1. Runtime gate checks (GrowthBook + OAuth + org policy + min version)
2. OAuth token validation with cross-process dead-token backoff
3. Session title derivation from conversation history
4. Git context gathering
5. v1 vs v2 routing based on GrowthBook gate
6. Session creation, archival, title sync injection
"""

from __future__ import annotations

import os
import socket
from typing import Any, Optional

from hare.bridge.bridge_config import (
    get_bridge_access_token,
    get_bridge_base_url,
)
from hare.bridge.bridge_enabled import (
    check_bridge_min_version,
    get_bridge_disabled_reason,
    is_cse_shim_enabled,
    is_env_less_bridge_enabled,
)
from hare.bridge.repl_bridge import BridgeState, ReplBridgeHandle, init_bridge_core
from hare.bridge.remote_bridge_core import init_env_less_bridge_core
from hare.bridge.session_id_compat import set_cse_shim_gate


async def init_repl_bridge(
    options: Optional[dict[str, Any]] = None,
    context: Optional[dict[str, Any]] = None,
) -> Optional[ReplBridgeHandle]:
    """Initialize the REPL bridge with full gate checks and session setup.

    Returns ReplBridgeHandle on success, None if bridge is not available.
    """
    opts = options or {}
    ctx = context or {}

    on_inbound_message = opts.get("on_inbound_message")
    on_permission_response = opts.get("on_permission_response")
    on_interrupt = opts.get("on_interrupt")
    on_set_model = opts.get("on_set_model")
    on_set_max_thinking_tokens = opts.get("on_set_max_thinking_tokens")
    on_set_permission_mode = opts.get("on_set_permission_mode")
    on_state_change = opts.get("on_state_change")
    initial_messages = opts.get("initial_messages")
    get_messages = opts.get("get_messages")
    previously_flushed_uuids = opts.get("previously_flushed_uuids")
    initial_name = opts.get("initial_name")
    perpetual = opts.get("perpetual", False)
    outbound_only = opts.get("outbound_only", False)
    tags = opts.get("tags")

    # Wire the cse_ shim kill switch
    set_cse_shim_gate(lambda: is_cse_shim_enabled(ctx))

    # 1. Runtime gate check
    disabled_reason = await get_bridge_disabled_reason(ctx)
    if disabled_reason:
        if on_state_change:
            on_state_change(BridgeState(error=disabled_reason), disabled_reason)
        return None

    # 2. Check OAuth
    access_token = get_bridge_access_token(ctx.get("get_claude_ai_oauth_tokens"))
    if not access_token:
        if on_state_change:
            on_state_change(BridgeState(error="/login"), "/login")
        return None

    base_url = get_bridge_base_url(ctx.get("get_oauth_config"))

    # 3. Check min version for v1 path
    current_version = ctx.get("version", "0.0.0")
    if not is_env_less_bridge_enabled(ctx):
        version_error = check_bridge_min_version(current_version, ctx)
        if version_error:
            if on_state_change:
                on_state_change(BridgeState(error=version_error), version_error)
            return None

    # 4. Derive session title
    title = initial_name or ""
    if not title and initial_messages:
        from hare.bridge.bridge_messaging import extract_title_text

        for msg in initial_messages:
            t = extract_title_text(msg)
            if t:
                title = t
                break

    # 5. Get git context
    git_repo_url = None
    branch = ""
    get_remote_url = ctx.get("get_remote_url")
    get_branch = ctx.get("get_branch")
    if get_remote_url:
        try:
            git_repo_url = await get_remote_url()
        except Exception:
            pass
    if get_branch:
        try:
            branch = await get_branch()
        except Exception:
            pass

    machine_name = socket.gethostname()

    # 6. Build bridge config
    from hare.bridge.types import BridgeConfig
    import uuid as _uuid

    config = BridgeConfig(
        dir=ctx.get("cwd", os.getcwd()),
        machine_name=machine_name,
        branch=branch,
        git_repo_url=git_repo_url,
        max_sessions=1,
        spawn_mode="single-session",
        bridge_id=str(_uuid.uuid4()),
        worker_type="claude_code",
        environment_id=str(_uuid.uuid4()),
        api_base_url=base_url,
        session_ingress_url=base_url,
    )

    # 7. Route to v1 or v2
    if is_env_less_bridge_enabled(ctx):
        # v2: env-less path
        return await init_env_less_bridge_core(
            config=config,
            access_token=access_token,
            base_url=base_url,
            title=title,
            initial_messages=initial_messages,
            on_inbound_message=on_inbound_message,
            on_permission_response=on_permission_response,
            on_interrupt=on_interrupt,
            on_set_model=on_set_model,
            on_set_max_thinking_tokens=on_set_max_thinking_tokens,
            on_set_permission_mode=on_set_permission_mode,
            on_state_change=on_state_change,
            get_access_token_fn=ctx.get("get_bridge_access_token"),
            outbound_only=outbound_only,
            tags=tags,
            previously_flushed_uuids=previously_flushed_uuids,
        )
    else:
        # v1: env-based path
        return await init_bridge_core(
            config=config,
            on_inbound_message=on_inbound_message,
            on_permission_response=on_permission_response,
            on_state_change=on_state_change,
            perpetual=perpetual,
            outbound_only=outbound_only,
            bridge_pointer_dir=os.getcwd(),
        )
