"""
Advisor tool feature flags and message types (`advisor.ts`).

SDK types for advisor blocks are approximated; GrowthBook/settings are stubbed.
"""

from __future__ import annotations

import os
from typing import Any, Literal, TypedDict, cast

from hare.utils.env_utils import is_env_truthy

# --- Block shapes (SDK may not expose these yet) ---


class AdvisorServerToolUseBlock(TypedDict):
    type: Literal["server_tool_use"]
    id: str
    name: Literal["advisor"]
    input: dict[str, Any]


class AdvisorResultContent(TypedDict):
    type: Literal["advisor_result"]
    text: str


class AdvisorRedactedContent(TypedDict):
    type: Literal["advisor_redacted_result"]
    encrypted_content: str


class AdvisorToolResultErrorContent(TypedDict):
    type: Literal["advisor_tool_result_error"]
    error_code: str


class AdvisorToolResultBlock(TypedDict):
    type: Literal["advisor_tool_result"]
    tool_use_id: str
    content: (
        AdvisorResultContent | AdvisorRedactedContent | AdvisorToolResultErrorContent
    )


AdvisorBlock = AdvisorServerToolUseBlock | AdvisorToolResultBlock


def is_advisor_block(param: dict[str, Any]) -> bool:
    t = param.get("type")
    if t == "advisor_tool_result":
        return True
    return t == "server_tool_use" and param.get("name") == "advisor"


class _AdvisorConfig(TypedDict, total=False):
    enabled: bool
    can_user_configure: bool
    base_model: str
    advisor_model: str


def _get_advisor_config() -> _AdvisorConfig:
    try:
        from hare.services.analytics.growthbook import (  # type: ignore[import-not-found]
            get_feature_value_cached_may_be_stale,
        )

        return cast(
            _AdvisorConfig,
            get_feature_value_cached_may_be_stale("tengu_sage_compass", {}),
        )
    except Exception:
        return {}


def is_advisor_enabled() -> bool:
    if is_env_truthy(os.environ.get("CLAUDE_CODE_DISABLE_ADVISOR_TOOL")):
        return False
    try:
        from hare.utils.model.providers import get_api_provider

        prov = get_api_provider()
        first_party_ok = prov in ("firstParty", "foundry")
        if not first_party_ok or is_env_truthy(
            os.environ.get("CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS")
        ):
            return False
    except Exception:
        return False
    return bool(_get_advisor_config().get("enabled", False))


def can_user_configure_advisor() -> bool:
    return is_advisor_enabled() and bool(
        _get_advisor_config().get("can_user_configure", False)
    )


def get_experiment_advisor_models() -> dict[str, str] | None:
    cfg = _get_advisor_config()
    if not (
        is_advisor_enabled()
        and not can_user_configure_advisor()
        and cfg.get("base_model")
        and cfg.get("advisor_model")
    ):
        return None
    return {"baseModel": cfg["base_model"], "advisorModel": cfg["advisor_model"]}


def model_supports_advisor(model: str) -> bool:
    m = model.lower()
    return "opus-4-6" in m or "sonnet-4-6" in m or os.environ.get("USER_TYPE") == "ant"


def is_valid_advisor_model(model: str) -> bool:
    return model_supports_advisor(model)


def get_initial_advisor_setting() -> str | None:
    if not is_advisor_enabled():
        return None
    try:
        from hare.utils.settings.settings import get_initial_settings

        return getattr(get_initial_settings(), "advisor_model", None)
    except Exception:
        return None


def get_advisor_usage(usage: dict[str, Any]) -> list[dict[str, Any]]:
    iterations = usage.get("iterations")
    if not iterations or not isinstance(iterations, list):
        return []
    return [
        cast(dict[str, Any], it)
        for it in iterations
        if it.get("type") == "advisor_message"
    ]


ADVISOR_TOOL_INSTRUCTIONS = """# Advisor Tool

You have access to an `advisor` tool backed by a stronger reviewer model. It takes NO parameters -- when you call it, your entire conversation history is automatically forwarded. The advisor sees the task, every tool call you've made, every result you've seen.

Call advisor BEFORE substantive work -- before writing code, before committing to an interpretation, before building on an assumption. If the task requires orientation first (finding files, reading code, seeing what's there), do that, then call advisor. Orientation is not substantive work. Writing, editing, and declaring an answer are.

Also call advisor:
- When you believe the task is complete. BEFORE this call, make your deliverable durable: write the file, stage the change, save the result. The advisor call takes time; if the session ends during it, a durable result persists and an unwritten one doesn't.
- When stuck -- errors recurring, approach not converging, results that don't fit.
- When considering a change of approach.

On tasks longer than a few steps, call advisor at least once before committing to an approach and once before declaring done. On short reactive tasks where the next action is dictated by tool output you just read, you don't need to keep calling -- the advisor adds most of its value on the first call, before the approach crystallizes.

Give the advice serious weight. If you follow a step and it fails empirically, or you have primary-source evidence that contradicts a specific claim (the file says X, the code does Y), adapt. A passing self-test is not evidence the advice is wrong -- it's evidence your test doesn't check what the advice is checking.

If you've already retrieved data pointing one way and the advisor points another: don't silently switch. Surface the conflict in one more advisor call -- "I found X, you suggest Y, which constraint breaks the tie?" The advisor saw your evidence but may have underweighted it; a reconcile call is cheaper than committing to the wrong branch."""
