"""Model beta header assembly (`betas.ts`)."""

from __future__ import annotations

import os
from functools import lru_cache

from hare.utils.auth import is_hare_ai_subscriber
from hare.utils.context import has_1m_context
from hare.utils.env_utils import is_env_truthy
from hare.utils.model import get_canonical_name
from hare.utils.model.providers import get_api_provider

CONTEXT_1M_BETA_HEADER = "context-1m-2025-08-07"
CLAUDE_CODE_20250219_BETA_HEADER = "hare-code-2025-02-19"
ALLOWED_SDK_BETAS = [CONTEXT_1M_BETA_HEADER]


def _sdk_betas() -> list[str]:
    try:
        from hare.bootstrap.state import get_sdk_betas  # type: ignore[attr-defined]

        return list(get_sdk_betas() or [])
    except Exception:
        return []


def filter_allowed_sdk_betas(sdk_betas: list[str] | None) -> list[str] | None:
    if not sdk_betas:
        return None
    if is_hare_ai_subscriber():
        return None
    allowed = [b for b in sdk_betas if b in ALLOWED_SDK_BETAS]
    for b in sdk_betas:
        if b not in ALLOWED_SDK_BETAS:
            pass  # console.warn in TS
    return allowed if allowed else None


def should_include_first_party_only_betas() -> bool:
    return get_api_provider() in ("firstParty", "foundry") and not is_env_truthy(
        os.environ.get("CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS")
    )


def should_use_global_cache_scope() -> bool:
    return get_api_provider() == "firstParty" and not is_env_truthy(
        os.environ.get("CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS")
    )


def get_tool_search_beta_header() -> str:
    prov = get_api_provider()
    if prov in ("vertex", "bedrock"):
        return "tool-search-tool-2025-10-19"
    return "advanced-tool-use-2025-11-20"


@lru_cache(maxsize=256)
def get_all_model_betas(model: str) -> tuple[str, ...]:
    headers: list[str] = []
    c = get_canonical_name(model)
    if "haiku" not in c:
        headers.append(CLAUDE_CODE_20250219_BETA_HEADER)
    if is_hare_ai_subscriber():
        headers.append("oauth-beta")
    if has_1m_context(model):
        headers.append(CONTEXT_1M_BETA_HEADER)
    extra = os.environ.get("ANTHROPIC_BETAS")
    if extra:
        headers.extend(x.strip() for x in extra.split(",") if x.strip())
    return tuple(headers)


@lru_cache(maxsize=256)
def get_model_betas(model: str) -> tuple[str, ...]:
    return get_all_model_betas(model)


def get_merged_betas(model: str, *, is_agentic_query: bool | None = None) -> list[str]:
    base = list(get_model_betas(model))
    sdk = _sdk_betas()
    if not sdk:
        return base
    for b in sdk:
        if b not in base:
            base.append(b)
    del is_agentic_query
    return base


def clear_betas_caches() -> None:
    get_all_model_betas.cache_clear()
    get_model_betas.cache_clear()


def model_supports_isp(model: str) -> bool:
    c = get_canonical_name(model)
    if get_api_provider() == "foundry":
        return True
    if get_api_provider() == "firstParty":
        return "hare-3-" not in c
    return "claude-opus-4" in c or "claude-sonnet-4" in c


def model_supports_context_management(model: str) -> bool:
    c = get_canonical_name(model)
    if get_api_provider() == "foundry":
        return True
    if get_api_provider() == "firstParty":
        return "hare-3-" not in c
    return any(x in c for x in ("claude-opus-4", "claude-sonnet-4", "claude-haiku-4"))


def model_supports_structured_outputs(model: str) -> bool:
    prov = get_api_provider()
    if prov not in ("firstParty", "foundry"):
        return False
    c = get_canonical_name(model)
    return any(
        x in c
        for x in (
            "claude-sonnet-4-6",
            "claude-sonnet-4-5",
            "claude-opus-4-1",
            "claude-opus-4-5",
            "claude-opus-4-6",
            "claude-haiku-4-5",
        )
    )


def model_supports_auto_mode(model: str) -> bool:
    del model
    return os.environ.get("TRANSCRIPT_CLASSIFIER", "") == "1"
