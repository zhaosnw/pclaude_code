"""Generate keybindings.json template (port of src/keybindings/template.ts)."""

from __future__ import annotations

import json

from hare.keybindings.default_bindings import DEFAULT_BINDINGS
from hare.keybindings.reserved_shortcuts import (
    NON_REBINDABLE,
    normalize_key_for_comparison,
)


def _filter_reserved(blocks: list) -> list:
    reserved = {normalize_key_for_comparison(r.key) for r in NON_REBINDABLE}
    out = []
    for block in blocks:
        filtered: dict[str, str | None] = {}
        for key, action in block.bindings.items():
            if normalize_key_for_comparison(key) not in reserved:
                filtered[key] = action
        if filtered:
            out.append({"context": block.context, "bindings": filtered})
    return out


def generate_keybindings_template() -> str:
    bindings = _filter_reserved(DEFAULT_BINDINGS)
    config = {
        "$schema": "https://www.schemastore.org/claude-code-keybindings.json",
        "$docs": "https://code.claude.com/docs/en/keybindings",
        "bindings": bindings,
    }
    return json.dumps(config, indent=2, ensure_ascii=False) + "\n"
