"""Parse `<hare-code-hint />` from tool output (`hare_code_hints.ts`)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from hare.utils.debug import log_for_debugging
from hare.utils.signal import create_signal

HareCodeHintType = Literal["plugin"]

SUPPORTED_VERSIONS = {1}
SUPPORTED_TYPES: set[str] = {"plugin"}

HINT_TAG_RE = re.compile(
    r"^[ \t]*<hare-code-hint\s+([^>]*?)\s*/>[ \t]*$",
    re.MULTILINE,
)
ATTR_RE = re.compile(r'(\w+)=(?:"([^"]*)"|([^\s/>]+))')


@dataclass
class HareCodeHint:
    v: int
    type: HareCodeHintType
    value: str
    source_command: str


_pending: HareCodeHint | None = None
_shown_this_session = False
_pending_changed = create_signal()


def _parse_attrs(tag_body: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for m in ATTR_RE.finditer(tag_body):
        key = m.group(1)
        val = m.group(2) if m.group(2) is not None else (m.group(3) or "")
        if key:
            out[key] = val
    return out


def _first_command_token(command: str) -> str:
    t = command.strip()
    idx = re.search(r"\s", t)
    if idx is None:
        return t
    return t[: idx.start()]


def extract_hare_code_hints(
    output: str, command: str
) -> tuple[list[HareCodeHint], str]:
    if "<hare-code-hint" not in output:
        return [], output

    source = _first_command_token(command)
    hints: list[HareCodeHint] = []

    def repl(m: re.Match[str]) -> str:
        raw_line = m.group(1) or ""
        attrs = _parse_attrs(raw_line)
        v_raw = attrs.get("v")
        typ = attrs.get("type")
        value = attrs.get("value")
        try:
            v = int(v_raw) if v_raw is not None else -1
        except ValueError:
            v = -1
        if v not in SUPPORTED_VERSIONS:
            log_for_debugging(
                f"[hare_code_hints] dropped hint with unsupported v={attrs.get('v')}"
            )
            return ""
        if not typ or typ not in SUPPORTED_TYPES:
            log_for_debugging(
                f"[hare_code_hints] dropped hint with unsupported type={typ}"
            )
            return ""
        if not value:
            log_for_debugging("[hare_code_hints] dropped hint with empty value")
            return ""
        hints.append(
            HareCodeHint(v=v, type="plugin", value=value, source_command=source)
        )
        return ""

    stripped = HINT_TAG_RE.sub(repl, output)
    if hints or stripped != output:
        stripped = re.sub(r"\n{3,}", "\n\n", stripped)
    return hints, stripped


def set_pending_hint(hint: HareCodeHint) -> None:
    global _pending
    if _shown_this_session:
        return
    _pending = hint
    _pending_changed.emit()


def clear_pending_hint() -> None:
    global _pending
    if _pending is not None:
        _pending = None
        _pending_changed.emit()


def mark_shown_this_session() -> None:
    global _shown_this_session
    _shown_this_session = True


subscribe_to_pending_hint = _pending_changed.subscribe


def get_pending_hint_snapshot() -> HareCodeHint | None:
    return _pending


def has_shown_hint_this_session() -> bool:
    return _shown_this_session


def _reset_hare_code_hint_store() -> None:
    global _pending, _shown_this_session
    _pending = None
    _shown_this_session = False


_test = {"parse_attrs": _parse_attrs, "first_command_token": _first_command_token}
