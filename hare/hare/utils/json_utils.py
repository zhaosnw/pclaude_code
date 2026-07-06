"""JSON / JSONL / JSONC helpers. Port of: src/utils/json.ts"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, TypedDict

from hare.utils.json_read import strip_bom
from hare.utils.log import log_error
from hare.utils.memoize import memoize_with_lru

PARSE_CACHE_MAX_KEY_BYTES = 8 * 1024


class _CachedParse(TypedDict, total=False):
    ok: bool
    value: Any


def _parse_json_uncached(json_str: str, should_log_error: bool) -> _CachedParse:
    try:
        return {"ok": True, "value": json.loads(strip_bom(json_str))}
    except Exception as e:
        if should_log_error:
            log_error(e)
        return {"ok": False}


_parse_json_cached: Callable[..., _CachedParse] = memoize_with_lru(
    _parse_json_uncached,
    lambda json_str, should_log_error: json_str,
    50,
)


def safe_parse_json(
    json_str: str | None,
    should_log_error: bool = True,
) -> Any:
    """Memoized JSON.parse with BOM strip; bounded LRU for small strings."""
    if not json_str:
        return None
    if len(json_str) > PARSE_CACHE_MAX_KEY_BYTES:
        result = _parse_json_uncached(json_str, should_log_error)
    else:
        result = _parse_json_cached(json_str, should_log_error)
    return result.get("value") if result.get("ok") else None


def safe_parse_jsonc(json_str: str | None) -> Any:
    """Parse JSON with comments — requires `jsonc_parser` for full parity; stub uses std json."""
    if not json_str:
        return None
    try:
        # Strip // and /* */ comments minimally for tests (full port: jsonc-parser)
        cleaned = _strip_jsonc_comments(strip_bom(json_str))
        return json.loads(cleaned)
    except Exception as e:
        log_error(e)
        return None


def _strip_jsonc_comments(s: str) -> str:
    out: list[str] = []
    i = 0
    in_str = False
    esc = False
    while i < len(s):
        c = s[i]
        if not in_str and s[i : i + 2] == "//":
            while i < len(s) and s[i] != "\n":
                i += 1
            continue
        if not in_str and s[i : i + 2] == "/*":
            i += 2
            while i + 1 < len(s) and s[i : i + 2] != "*/":
                i += 1
            i += 2 if i + 1 < len(s) else 1
            continue
        if c == '"' and (i == 0 or s[i - 1] != "\\"):
            in_str = not in_str
        out.append(c)
        i += 1
    return "".join(out)


def parse_jsonl(data: str | bytes | memoryview) -> list[Any]:
    if isinstance(data, memoryview):
        data = data.tobytes()
    if isinstance(data, bytes):
        start = (
            3
            if len(data) >= 3
            and data[0] == 0xEF
            and data[1] == 0xBB
            and data[2] == 0xBF
            else 0
        )
        text = data[start:].decode("utf-8", errors="replace")
    else:
        text = strip_bom(data)
    results: list[Any] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            results.append(json.loads(line))
        except Exception:
            pass
    return results


MAX_JSONL_READ_BYTES = 100 * 1024 * 1024


async def read_jsonl_file(file_path: str) -> list[Any]:
    p = Path(file_path)
    size = p.stat().st_size
    if size <= MAX_JSONL_READ_BYTES:
        return parse_jsonl(p.read_bytes())
    with p.open("rb") as f:
        f.seek(max(0, size - MAX_JSONL_READ_BYTES))
        buf = f.read()
    nl = buf.find(b"\n")
    if nl != -1 and nl < len(buf) - 1:
        return parse_jsonl(buf[nl + 1 :])
    return parse_jsonl(buf)


def add_item_to_jsonc_array(content: str, new_item: Any) -> str:
    """Append to JSON array in JSONC file — preserves comments if `jsonc_parser` available."""
    try:
        if not content or not content.strip():
            return json.dumps([new_item], indent=4)
        clean = strip_bom(content)
        parsed = json.loads(_strip_jsonc_comments(clean))
        if isinstance(parsed, list):
            parsed.append(new_item)
            return json.dumps(parsed, indent=4)
        return json.dumps([new_item], indent=4)
    except Exception as e:
        log_error(e)
        return json.dumps([new_item], indent=4)


def json_stringify(obj: Any, indent: int | None = None) -> str:
    if indent is None:
        return json.dumps(obj, default=str, separators=(",", ":"))
    return json.dumps(obj, indent=indent, default=str)
