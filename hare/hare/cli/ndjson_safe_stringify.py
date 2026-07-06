"""NDJSON-safe JSON serialization (port of src/cli/ndjsonSafeStringify.ts)."""

from __future__ import annotations

import json
from typing import Any

_JS_LINE_TERMINATORS = ("\u2028", "\u2029")


def ndjson_safe_stringify(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    for sep in _JS_LINE_TERMINATORS:
        raw = raw.replace(sep, "\\u2028" if sep == "\u2028" else "\\u2029")
    return raw
