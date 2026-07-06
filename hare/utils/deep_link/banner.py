"""Port of: src/utils/deepLink/banner.ts"""

from __future__ import annotations


def get_deep_link_banner(action: str) -> str:
    return f"Deep link: {action}"
