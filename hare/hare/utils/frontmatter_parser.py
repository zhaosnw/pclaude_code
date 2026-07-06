"""
Markdown YAML frontmatter parsing.

Port of: src/utils/frontmatterParser.ts
"""

from __future__ import annotations

import re
from typing import Any

from hare.utils.debug import log_for_debugging

FRONTMATTER_REGEX = re.compile(r"^---\s*\n([\s\S]*?)---\s*\n?", re.MULTILINE)

YAML_SPECIAL_CHARS = re.compile(r"[{}[\]*&#!|>%@`]|: ")


def _parse_yaml(text: str) -> Any:
    try:
        import yaml

        return yaml.safe_load(text)
    except ImportError:
        log_for_debugging(
            "PyYAML not installed; frontmatter parsing disabled", level="warn"
        )
        return None


def _quote_problematic_values(frontmatter_text: str) -> str:
    lines = frontmatter_text.split("\n")
    out: list[str] = []
    for line in lines:
        m = re.match(r"^([a-zA-Z_-]+):\s+(.+)$", line)
        if not m:
            out.append(line)
            continue
        key, value = m.group(1), m.group(2)
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            out.append(line)
            continue
        if YAML_SPECIAL_CHARS.search(value):
            esc = value.replace("\\", "\\\\").replace('"', '\\"')
            out.append(f'{key}: "{esc}"')
        else:
            out.append(line)
    return "\n".join(out)


def parse_frontmatter(markdown: str, source_path: str | None = None) -> dict[str, Any]:
    m = FRONTMATTER_REGEX.match(markdown)
    if not m:
        return {"frontmatter": {}, "content": markdown}
    fm_text = m.group(1) or ""
    content = markdown[m.end() :]
    frontmatter: dict[str, Any] = {}
    try:
        parsed = _parse_yaml(fm_text)
        if isinstance(parsed, dict):
            frontmatter = parsed
    except Exception:  # noqa: BLE001
        try:
            parsed = _parse_yaml(_quote_problematic_values(fm_text))
            if isinstance(parsed, dict):
                frontmatter = parsed
        except Exception as e2:  # noqa: BLE001
            loc = f" in {source_path}" if source_path else ""
            log_for_debugging(
                f"Failed to parse YAML frontmatter{loc}: {e2}", level="warn"
            )
    return {"frontmatter": frontmatter, "content": content}


def split_path_in_frontmatter(input_val: str | list[str]) -> list[str]:
    if isinstance(input_val, list):
        out: list[str] = []
        for x in input_val:
            out.extend(split_path_in_frontmatter(x))
        return out
    if not isinstance(input_val, str):
        return []
    parts: list[str] = []
    current = ""
    brace_depth = 0
    for char in input_val:
        if char == "{":
            brace_depth += 1
            current += char
        elif char == "}":
            brace_depth -= 1
            current += char
        elif char == "," and brace_depth == 0:
            t = current.strip()
            if t:
                parts.append(t)
            current = ""
        else:
            current += char
    t = current.strip()
    if t:
        parts.append(t)
    out: list[str] = []
    for x in parts:
        out.extend(_expand_braces(x))
    return out


def _expand_braces(pattern: str) -> list[str]:
    m = re.match(r"^([^{]*)\{([^}]+)\}(.*)$", pattern)
    if not m:
        return [pattern]
    prefix, alts, suffix = m.group(1), m.group(2), m.group(3)
    expanded: list[str] = []
    for part in (a.strip() for a in alts.split(",")):
        combined = prefix + part + suffix
        expanded.extend(_expand_braces(combined))
    return expanded


def parse_positive_int_from_frontmatter(value: object) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value) if isinstance(value, int) else int(str(value), 10)
    except (ValueError, TypeError):
        return None
    return parsed if parsed > 0 else None


def coerce_description_to_string(
    value: object,
    component_name: str | None = None,
    plugin_name: str | None = None,
) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, (int, float, bool)):
        return str(value)
    src = (
        f"{plugin_name}:{component_name}"
        if plugin_name
        else (component_name or "unknown")
    )
    log_for_debugging(f"Description invalid for {src} - omitting", level="warn")
    return None


def parse_boolean_frontmatter(value: object) -> bool:
    return value is True or value == "true"


FrontmatterShell = str  # 'bash' | 'powershell'

_FRONTMATTER_SHELLS = frozenset({"bash", "powershell"})


def parse_shell_frontmatter(value: object, source: str) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if not normalized:
        return None
    if normalized in _FRONTMATTER_SHELLS:
        return normalized
    log_for_debugging(
        f"Frontmatter 'shell: {value}' in {source} is not recognized. "
        f"Valid values: {', '.join(sorted(_FRONTMATTER_SHELLS))}. Falling back to bash.",
        level="warn",
    )
    return None
