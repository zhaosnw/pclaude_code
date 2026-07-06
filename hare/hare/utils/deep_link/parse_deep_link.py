"""
Deep link URI parser for `claude-cli://open`.

Port of: src/utils/deepLink/parseDeepLink.ts
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

from hare.utils.sanitization import partially_sanitize_unicode

DEEP_LINK_PROTOCOL = "claude-cli"
REPO_SLUG_PATTERN = re.compile(r"^[\w.-]+/[\w.-]+$")
MAX_QUERY_LENGTH = 5000
MAX_CWD_LENGTH = 4096


@dataclass
class DeepLinkAction:
    query: str | None = None
    cwd: str | None = None
    repo: str | None = None


def _contains_control_chars(s: str) -> bool:
    for ch in s:
        o = ord(ch)
        if o <= 0x1F or o == 0x7F:
            return True
    return False


def parse_deep_link(uri: str) -> DeepLinkAction:
    if uri.startswith(f"{DEEP_LINK_PROTOCOL}://"):
        normalized = uri
    elif uri.startswith(f"{DEEP_LINK_PROTOCOL}:"):
        normalized = uri.replace(
            f"{DEEP_LINK_PROTOCOL}:", f"{DEEP_LINK_PROTOCOL}://", 1
        )
    else:
        raise ValueError(
            f'Invalid deep link: expected {DEEP_LINK_PROTOCOL}:// scheme, got "{uri}"'
        )

    parsed = urlparse(normalized)
    if parsed.scheme != DEEP_LINK_PROTOCOL:
        raise ValueError(f'Invalid deep link URL: "{uri}"')
    if parsed.hostname != "open":
        raise ValueError(f'Unknown deep link action: "{parsed.hostname}"')

    qs = parse_qs(parsed.query)
    cwd_l = qs.get("cwd", [None])[0]
    repo_l = qs.get("repo", [None])[0]
    raw_q = qs.get("q", [None])[0]

    cwd = cwd_l
    repo = repo_l

    if cwd and not cwd.startswith("/") and not re.match(r"^[a-zA-Z]:[/\\]", cwd):
        raise ValueError(
            f'Invalid cwd in deep link: must be an absolute path, got "{cwd}"'
        )

    if cwd and _contains_control_chars(cwd):
        raise ValueError("Deep link cwd contains disallowed control characters")
    if cwd and len(cwd) > MAX_CWD_LENGTH:
        raise ValueError(
            f"Deep link cwd exceeds {MAX_CWD_LENGTH} characters (got {len(cwd)})"
        )

    if repo and not REPO_SLUG_PATTERN.match(repo):
        raise ValueError(
            f'Invalid repo in deep link: expected "owner/repo", got "{repo}"'
        )

    query: str | None = None
    if raw_q and raw_q.strip():
        query = partially_sanitize_unicode(raw_q.strip())
        if _contains_control_chars(query):
            raise ValueError("Deep link query contains disallowed control characters")
        if len(query) > MAX_QUERY_LENGTH:
            raise ValueError(
                f"Deep link query exceeds {MAX_QUERY_LENGTH} characters (got {len(query)})"
            )

    return DeepLinkAction(query=query, cwd=cwd, repo=repo)


def build_deep_link(action: DeepLinkAction) -> str:
    from urllib.parse import urlencode

    base = f"{DEEP_LINK_PROTOCOL}://open"
    params: dict[str, str] = {}
    if action.query:
        params["q"] = action.query
    if action.cwd:
        params["cwd"] = action.cwd
    if action.repo:
        params["repo"] = action.repo
    if not params:
        return base
    return f"{base}?{urlencode(params)}"
