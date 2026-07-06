"""Lazy cli-highlight / highlight.js load (`cliHighlight.ts`)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Protocol

_cli_highlight: Any | None = None
_load_lock = asyncio.Lock()
_loaded_get_language: Any | None = None


class _CliHighlight(Protocol):
    def highlight(self, text: str, *args: Any, **kwargs: Any) -> str: ...
    def supports_language(self, lang: str) -> bool: ...


async def _load() -> _CliHighlight | None:
    global _loaded_get_language
    try:
        import cli_highlight as ch  # type: ignore[import-not-found]
        import highlightjs as hljs  # type: ignore[import-not-found]

        _loaded_get_language = hljs.get_language
        return ch  # type: ignore[return-value]
    except ImportError:
        return None


async def get_cli_highlight() -> _CliHighlight | None:
    global _cli_highlight
    async with _load_lock:
        if _cli_highlight is None:
            _cli_highlight = await _load()
        return _cli_highlight


async def get_language_name(file_path: str) -> str:
    await get_cli_highlight()
    ext = Path(file_path).suffix[1:]
    if not ext:
        return "unknown"
    if _loaded_get_language is None:
        return "unknown"
    lang = _loaded_get_language(ext)
    return getattr(lang, "name", "unknown") if lang else "unknown"
