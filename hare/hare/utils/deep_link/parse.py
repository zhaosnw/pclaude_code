"""
Deep link parsing.

Port of: src/utils/deepLink/parseDeepLink.ts
"""

from __future__ import annotations
from urllib.parse import urlparse, parse_qs
from dataclasses import dataclass

PROTOCOL = "hare-code"


@dataclass
class DeepLink:
    action: str = ""
    params: dict[str, str] = None  # type: ignore
    raw_url: str = ""

    def __post_init__(self) -> None:
        if self.params is None:
            self.params = {}


def parse_deep_link(url: str) -> DeepLink | None:
    if not url.startswith(f"{PROTOCOL}://"):
        return None
    parsed = urlparse(url)
    action = parsed.hostname or parsed.path.strip("/")
    params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    return DeepLink(action=action, params=params, raw_url=url)
