"""
Whether a request is billed as "extra usage" (subscriber / fast mode rules).

Port of: src/utils/extraUsage.ts
"""

from __future__ import annotations

import re


def _is_hare_ai_subscriber() -> bool:
    try:
        from hare.utils.auth import is_hare_ai_subscriber

        return is_hare_ai_subscriber()
    except ImportError:
        return False


def _has_1m_context(model: str) -> bool:
    try:
        from hare.utils.context import has_1m_context

        return has_1m_context(model)
    except ImportError:
        return False


def is_billed_as_extra_usage(
    model: str | None,
    is_fast_mode: bool,
    is_opus_1m_merged: bool,
) -> bool:
    if not _is_hare_ai_subscriber():
        return False
    if is_fast_mode:
        return True
    if model is None or not _has_1m_context(model):
        return False
    m = re.sub(r"\[1m\]$", "", model.lower()).strip()
    is_opus_46 = m == "opus" or "opus-4-6" in m
    is_sonnet_46 = m == "sonnet" or "sonnet-4-6" in m
    if is_opus_46 and is_opus_1m_merged:
        return False
    return is_opus_46 or is_sonnet_46
