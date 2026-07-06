"""Keybinding validation (port of src/keybindings/validate.ts)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from hare.keybindings.parser import chord_to_string, parse_keystroke
from hare.keybindings.reserved_shortcuts import (
    get_reserved_shortcuts,
    normalize_key_for_comparison,
)
from hare.keybindings.types import KeybindingBlock, KeybindingContextName, ParsedBinding


def _plural(n: int, word: str) -> str:
    return f"{n} {word}s" if n != 1 else f"1 {word}"


KeybindingWarningType = Literal[
    "parse_error",
    "duplicate",
    "reserved",
    "invalid_context",
    "invalid_action",
]


@dataclass
class KeybindingWarning:
    type: KeybindingWarningType
    severity: Literal["error", "warning"]
    message: str
    key: str | None = None
    context: str | None = None
    action: str | None = None
    suggestion: str | None = None


VALID_CONTEXTS: list[KeybindingContextName] = [
    "Global",
    "Chat",
    "Autocomplete",
    "Confirmation",
    "Help",
    "Transcript",
    "HistorySearch",
    "Task",
    "ThemePicker",
    "Settings",
    "Tabs",
    "Attachments",
    "Footer",
    "MessageSelector",
    "DiffDialog",
    "ModelPicker",
    "Select",
    "Plugin",
    "Scroll",
    "MessageActions",
]


def _is_valid_context(value: str) -> bool:
    return value in VALID_CONTEXTS


def _validate_keystroke(keystroke: str) -> KeybindingWarning | None:
    for part in keystroke.lower().split("+"):
        if not part.strip():
            return KeybindingWarning(
                type="parse_error",
                severity="error",
                message=f'Empty key part in "{keystroke}"',
                key=keystroke,
                suggestion='Remove extra "+" characters',
            )
    parsed = parse_keystroke(keystroke)
    if not parsed.key and not (
        parsed.ctrl or parsed.alt or parsed.shift or parsed.meta
    ):
        return KeybindingWarning(
            type="parse_error",
            severity="error",
            message=f'Could not parse keystroke "{keystroke}"',
            key=keystroke,
        )
    return None


def _validate_block(block: object, block_index: int) -> list[KeybindingWarning]:
    warnings: list[KeybindingWarning] = []
    if not isinstance(block, dict):
        warnings.append(
            KeybindingWarning(
                type="parse_error",
                severity="error",
                message=f"Keybinding block {block_index + 1} is not an object",
            )
        )
        return warnings
    b = block
    raw_context = b.get("context")
    context_name: str | None = None
    if not isinstance(raw_context, str):
        warnings.append(
            KeybindingWarning(
                type="parse_error",
                severity="error",
                message=f'Keybinding block {block_index + 1} missing "context" field',
            )
        )
    elif not _is_valid_context(raw_context):
        warnings.append(
            KeybindingWarning(
                type="invalid_context",
                severity="error",
                message=f'Unknown context "{raw_context}"',
                context=raw_context,
                suggestion=f"Valid contexts: {', '.join(VALID_CONTEXTS)}",
            )
        )
    else:
        context_name = raw_context

    bindings = b.get("bindings")
    if not isinstance(bindings, dict):
        warnings.append(
            KeybindingWarning(
                type="parse_error",
                severity="error",
                message=f'Keybinding block {block_index + 1} missing "bindings" field',
            )
        )
        return warnings

    for key, action in bindings.items():
        err = _validate_keystroke(key)
        if err:
            err.context = context_name
            warnings.append(err)
        if action is not None and not isinstance(action, str):
            warnings.append(
                KeybindingWarning(
                    type="invalid_action",
                    severity="error",
                    message=f'Invalid action for "{key}": must be a string or null',
                    key=key,
                    context=context_name,
                )
            )
        elif isinstance(action, str) and action.startswith("command:"):
            if not re.match(r"^command:[a-zA-Z0-9:\-_]+$", action):
                warnings.append(
                    KeybindingWarning(
                        type="invalid_action",
                        severity="warning",
                        message=(
                            f'Invalid command binding "{action}" for "{key}": '
                            "command name may only contain alphanumeric characters, colons, hyphens, and underscores"
                        ),
                        key=key,
                        context=context_name,
                        action=action,
                    )
                )
            if context_name and context_name != "Chat":
                warnings.append(
                    KeybindingWarning(
                        type="invalid_action",
                        severity="warning",
                        message=(
                            f'Command binding "{action}" must be in "Chat" context, '
                            f'not "{context_name}"'
                        ),
                        key=key,
                        context=context_name,
                        action=action,
                        suggestion='Move this binding to a block with "context": "Chat"',
                    )
                )
    return warnings


def check_duplicate_keys_in_json(json_string: str) -> list[KeybindingWarning]:
    warnings: list[KeybindingWarning] = []
    pattern = re.compile(
        r'"bindings"\s*:\s*\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}', re.DOTALL
    )
    for m in pattern.finditer(json_string):
        block_content = m.group(1)
        if not block_content:
            continue
        text_before = json_string[: m.start()]
        ctx_m = re.search(r'"context"\s*:\s*"([^"]+)"[^{]*$', text_before)
        context = ctx_m.group(1) if ctx_m else "unknown"
        keys_by_name: dict[str, int] = {}
        for km in re.finditer(r'"([^"]+)"\s*:', block_content):
            key = km.group(1)
            c = keys_by_name.get(key, 0) + 1
            keys_by_name[key] = c
            if c == 2:
                warnings.append(
                    KeybindingWarning(
                        type="duplicate",
                        severity="warning",
                        message=f'Duplicate key "{key}" in {context} bindings',
                        key=key,
                        context=context,
                        suggestion=(
                            "This key appears multiple times in the same context. "
                            "JSON uses the last value, earlier values are ignored."
                        ),
                    )
                )
    return warnings


def validate_user_config(user_blocks: object) -> list[KeybindingWarning]:
    warnings: list[KeybindingWarning] = []
    if not isinstance(user_blocks, list):
        warnings.append(
            KeybindingWarning(
                type="parse_error",
                severity="error",
                message="keybindings.json must contain an array",
                suggestion="Wrap your bindings in [ ]",
            )
        )
        return warnings
    for i, block in enumerate(user_blocks):
        warnings.extend(_validate_block(block, i))
    return warnings


def check_duplicates(blocks: list[KeybindingBlock]) -> list[KeybindingWarning]:
    warnings: list[KeybindingWarning] = []
    seen_by_context: dict[str, dict[str, str]] = {}
    for block in blocks:
        ctx_map = seen_by_context.setdefault(block.context, {})
        for key, action in block.bindings.items():
            norm = normalize_key_for_comparison(key)
            existing = ctx_map.get(norm)
            if existing is not None and existing != (action or "null"):
                warnings.append(
                    KeybindingWarning(
                        type="duplicate",
                        severity="warning",
                        message=f'Duplicate binding "{key}" in {block.context} context',
                        key=key,
                        context=block.context,
                        action=action if isinstance(action, str) else "null (unbind)",
                        suggestion=f'Previously bound to "{existing}". Only the last binding will be used.',
                    )
                )
            ctx_map[norm] = action if isinstance(action, str) else "null"
    return warnings


def check_reserved_shortcuts(bindings: list[ParsedBinding]) -> list[KeybindingWarning]:
    warnings: list[KeybindingWarning] = []
    reserved = get_reserved_shortcuts()
    for binding in bindings:
        key_display = chord_to_string(binding.chord)
        normalized = normalize_key_for_comparison(key_display)
        for res in reserved:
            if normalize_key_for_comparison(res.key) == normalized:
                warnings.append(
                    KeybindingWarning(
                        type="reserved",
                        severity=res.severity,
                        message=f'"{key_display}" may not work: {res.reason}',
                        key=key_display,
                        context=binding.context,
                        action=binding.action or None,
                    )
                )
    return warnings


def _user_bindings_for_validation(
    user_blocks: list[KeybindingBlock],
) -> list[ParsedBinding]:
    out: list[ParsedBinding] = []
    for block in user_blocks:
        for key, action in block.bindings.items():
            chord = [parse_keystroke(k) for k in key.split(" ")]
            out.append(ParsedBinding(chord=chord, action=action, context=block.context))
    return out


def validate_bindings(
    user_blocks: object,
    _parsed_bindings: list[ParsedBinding],
) -> list[KeybindingWarning]:
    warnings: list[KeybindingWarning] = []
    warnings.extend(validate_user_config(user_blocks))
    if isinstance(user_blocks, list) and all(
        isinstance(b, KeybindingBlock) or isinstance(b, dict) for b in user_blocks
    ):
        blocks_typed: list[KeybindingBlock] = []
        for b in user_blocks:
            if isinstance(b, KeybindingBlock):
                blocks_typed.append(b)
            elif isinstance(b, dict) and "context" in b and "bindings" in b:
                blocks_typed.append(
                    KeybindingBlock(
                        context=b["context"],  # type: ignore[arg-type]
                        bindings=b["bindings"],  # type: ignore[arg-type]
                    )
                )
        if blocks_typed:
            warnings.extend(check_duplicates(blocks_typed))
            warnings.extend(
                check_reserved_shortcuts(_user_bindings_for_validation(blocks_typed))
            )
    seen: set[str] = set()

    def _dedup(w: KeybindingWarning) -> bool:
        k = f"{w.type}:{w.key}:{w.context}"
        if k in seen:
            return False
        seen.add(k)
        return True

    return [w for w in warnings if _dedup(w)]


def format_warning(warning: KeybindingWarning) -> str:
    icon = "✗" if warning.severity == "error" else "⚠"
    msg = f"{icon} Keybinding {warning.severity}: {warning.message}"
    if warning.suggestion:
        msg += f"\n  {warning.suggestion}"
    return msg


def format_warnings(warnings: list[KeybindingWarning]) -> str:
    if not warnings:
        return ""
    errors = [w for w in warnings if w.severity == "error"]
    warns = [w for w in warnings if w.severity == "warning"]
    lines: list[str] = []
    if errors:
        lines.append(f"Found {len(errors)} keybinding {_plural(len(errors), 'error')}:")
        lines.extend(format_warning(e) for e in errors)
    if warns:
        if lines:
            lines.append("")
        lines.append(f"Found {len(warns)} keybinding {_plural(len(warns), 'warning')}:")
        lines.extend(format_warning(w) for w in warns)
    return "\n".join(lines)
