"""Intl caching helpers — port of `intl.ts` (simplified; no stdlib Intl.Segmenter)."""

from __future__ import annotations

import locale
from datetime import datetime
from typing import Any

try:
    import regex as regex_lib  # type: ignore[import-not-found]
except ImportError:
    regex_lib = None

_grapheme_segmenter: Any = None
_word_segmenter: Any = None
_rtf_cache: dict[str, Any] = {}
_cached_tz: str | None = None
_cached_lang: str | None | object = None


def get_grapheme_segmenter() -> Any:
    global _grapheme_segmenter
    if _grapheme_segmenter is None:
        _grapheme_segmenter = object()
    return _grapheme_segmenter


def first_grapheme(text: str) -> str:
    if not text:
        return ""
    if regex_lib is not None:
        m = regex_lib.match(r"\X", text)
        if m:
            return m.group(0)
    return text[0]


def last_grapheme(text: str) -> str:
    if not text:
        return ""
    if regex_lib is not None:
        matches = list(regex_lib.finditer(r"\X", text))
        if matches:
            return matches[-1].group(0)
    return text[-1]


def get_word_segmenter() -> Any:
    global _word_segmenter
    if _word_segmenter is None:
        _word_segmenter = object()
    return _word_segmenter


def get_relative_time_format(style: str, numeric: str) -> Any:
    key = f"{style}:{numeric}"
    if key not in _rtf_cache:
        try:
            from babel.dates import format_timedelta  # type: ignore[import-not-found]

            _rtf_cache[key] = format_timedelta
        except ImportError:

            def _fallback(**_: Any) -> str:
                return ""

            _rtf_cache[key] = _fallback
    return _rtf_cache[key]


def get_time_zone() -> str:
    global _cached_tz
    if _cached_tz is None:
        _cached_tz = datetime.now().astimezone().tzname() or "UTC"
    return _cached_tz


def get_system_locale_language() -> str | None:
    global _cached_lang
    if _cached_lang is None:
        try:
            loc, _ = locale.getlocale()
            if loc:
                _cached_lang = loc.split("_")[0]
            else:
                _cached_lang = None
        except Exception:
            _cached_lang = None
    if _cached_lang is None:
        return None
    return str(_cached_lang)
