"""Fire-and-forget warmup to Anthropic API base URL (`apiPreconnect.ts`)."""

from __future__ import annotations

import os

from hare.utils.env_utils import is_env_truthy

_fired = False


def preconnect_anthropic_api() -> None:
    global _fired
    if _fired:
        return
    _fired = True
    if (
        is_env_truthy(os.environ.get("CLAUDE_CODE_USE_BEDROCK"))
        or is_env_truthy(os.environ.get("CLAUDE_CODE_USE_VERTEX"))
        or is_env_truthy(os.environ.get("CLAUDE_CODE_USE_FOUNDRY"))
    ):
        return
    if any(
        os.environ.get(k)
        for k in (
            "HTTPS_PROXY",
            "https_proxy",
            "HTTP_PROXY",
            "http_proxy",
            "ANTHROPIC_UNIX_SOCKET",
            "CLAUDE_CODE_CLIENT_CERT",
            "CLAUDE_CODE_CLIENT_KEY",
        )
    ):
        return
    base_url = os.environ.get("ANTHROPIC_BASE_URL") or "https://api.anthropic.com"

    async def _run() -> None:
        try:
            import aiohttp

            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                await session.head(base_url)
        except Exception:
            pass

    try:
        import asyncio

        asyncio.get_event_loop().create_task(_run())
    except RuntimeError:
        import threading

        def _thread() -> None:
            try:
                import urllib.request

                req = urllib.request.Request(base_url, method="HEAD")
                urllib.request.urlopen(req, timeout=10)
            except Exception:
                pass

        threading.Thread(target=_thread, daemon=True).start()
