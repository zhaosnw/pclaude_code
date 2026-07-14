#!/usr/bin/env python3
"""Generate and validate the recovered TS reference parity matrix.

The matrix covers four dimensions extracted from the recovered TS sources:
CLI options/subcommands, the tool catalog, hook events, and settings
permission keys. Every generated row starts as ``implemented-unverified``; an
item becomes ``aligned`` only after a golden case is recorded from the TS
reference and named in the evidence column.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REFERENCE_ROOT = PROJECT_ROOT / "recovered-from-cli-js-map" / "src"
DEFAULT_MATRIX = PROJECT_ROOT / "docs" / "alignment-status" / "parity-matrix.md"
CASES_DIR = PROJECT_ROOT / "hare" / "alignment" / "cases"

OPTION_RE = re.compile(r"\.option\(\s*(['\"])(?P<spec>.*?)\1", re.DOTALL)
COMMAND_RE = re.compile(r"\.command\(\s*(['\"])(?P<spec>.*?)\1", re.DOTALL)
LONG_OPTION_RE = re.compile(r"--[a-z][a-z0-9-]*")
TABLE_ROW_RE = re.compile(
    r"^\|\s*`(?P<feature>[^`]+)`\s*\|\s*`(?P<status>[^`]+)`\s*"
    r"\|\s*`?(?P<evidence>[^|`]*)`?\s*\|\s*`?(?P<priority>[^|`]*)`?\s*\|\s*$"
)

# Only map a feature here after its core CLI behavior is exercised by a
# TS-recorded golden. The map keeps regeneration deterministic while allowing
# the matrix to measure verified progress instead of resetting every row.
ALIGNED_EVIDENCE = {
    "cli.--continue": "session.continue_basic",
    "cli.--resume": "session.resume_basic",
    "cli.--permission-mode": "permission.mode_bypass",
    "cli.--mcp-config": "mcp.stdio_tool_call",
    "hook.PreToolUse": "hooks.pretool_block,hooks.pretool_allow",
    "hook.PostToolUse": "hooks.posttool_output",
    "hook.Stop": "hooks.stop_hook",
    # NOT listed: tool.AgentTool. subagent.task_dispatch is recorded but is a
    # known_divergence (parent/subagent state is not isolated), and a
    # known_divergence is evidence of a gap, not of alignment.
    "settings.permissions.allow": "permission.settings_allow_bash",
    "settings.permissions.deny": "permission.settings_deny_read",
}
# compact.auto_threshold passes but has no matrix row of its own: auto-compact
# is a runtime behavior, not a CLI flag / tool / hook / settings key. Add a
# behavior dimension before claiming it here.

# Hook events the reference can emit (coreTypes.ts HOOK_EVENTS). Tool-lifecycle
# and session-lifecycle events are the ones a headless code agent must honor.
P1_HOOK_EVENTS = {
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "Stop",
    "SubagentStop",
    "UserPromptSubmit",
    "SessionStart",
    "SessionEnd",
    "PreCompact",
    "PostCompact",
}
HOOK_EVENTS_RE = re.compile(
    r"export const HOOK_EVENTS = \[(?P<body>.*?)\]", re.DOTALL
)
QUOTED_RE = re.compile(r"'([A-Za-z]+)'")

# Settings keys that gate agent behavior. The recovered settings schema is
# large and mostly telemetry/UI; these are the ones alignment cases can prove.
SETTINGS_KEYS = [
    ("settings.permissions.allow", "P1"),
    ("settings.permissions.deny", "P1"),
    ("settings.permissions.ask", "P1"),
    ("settings.permissions.defaultMode", "P1"),
    ("settings.permissions.additionalDirectories", "P2"),
    ("settings.hooks", "P1"),
    ("settings.env", "P2"),
    ("settings.model", "P2"),
]


def _source_text(path: Path) -> str:
    if not path.is_file():
        raise ValueError(f"TS reference source is missing: {path}")
    return path.read_text(encoding="utf-8")


def extract_cli_features() -> list[tuple[str, str]]:
    """Return stable ``(feature, priority)`` rows from recovered CLI sources."""
    sources = [
        REFERENCE_ROOT / "main.tsx",
        REFERENCE_ROOT / "entrypoints" / "cli.tsx",
    ]
    options: set[str] = set()
    commands: set[str] = set()

    for source in sources:
        text = _source_text(source)
        for match in OPTION_RE.finditer(text):
            options.update(LONG_OPTION_RE.findall(match.group("spec")))
        for match in COMMAND_RE.finditer(text):
            name = match.group("spec").strip().split(maxsplit=1)[0]
            if name and not name.startswith("-"):
                commands.add(name)

    rows: list[tuple[str, str]] = []
    for option in sorted(options):
        priority = "P0" if option in {"--print", "--output-format", "--max-turns"} else "P1"
        rows.append((f"cli.{option}", priority))
    for command in sorted(commands):
        rows.append((f"cli.command.{command}", "P1"))
    return rows


def extract_tool_features() -> list[tuple[str, str]]:
    """Return top-level recovered tool directories as parity features."""
    tools_dir = REFERENCE_ROOT / "tools"
    if not tools_dir.is_dir():
        raise ValueError(f"TS reference tools directory is missing: {tools_dir}")

    core_tools = {
        "BashTool",
        "FileEditTool",
        "FileReadTool",
        "FileWriteTool",
        "GlobTool",
        "GrepTool",
        "LSTool",
    }
    return [
        (f"tool.{path.name}", "P0" if path.name in core_tools else "P1")
        for path in sorted(tools_dir.iterdir(), key=lambda item: item.name)
        if path.is_dir() and not path.name.startswith(".")
    ]


def extract_hook_features() -> list[tuple[str, str]]:
    """Return hook events declared in the recovered SDK core types."""
    text = _source_text(REFERENCE_ROOT / "entrypoints" / "sdk" / "coreTypes.ts")
    match = HOOK_EVENTS_RE.search(text)
    if match is None:
        raise ValueError("TS reference no longer declares HOOK_EVENTS")
    events = QUOTED_RE.findall(match.group("body"))
    if not events:
        raise ValueError("TS reference HOOK_EVENTS list is empty")
    return [
        (f"hook.{event}", "P1" if event in P1_HOOK_EVENTS else "P2")
        for event in sorted(set(events))
    ]


def extract_settings_features() -> list[tuple[str, str]]:
    """Return the behavior-gating settings keys tracked for alignment."""
    return list(SETTINGS_KEYS)


def render_matrix() -> str:
    rows = sorted(
        {
            *extract_cli_features(),
            *extract_tool_features(),
            *extract_hook_features(),
            *extract_settings_features(),
        }
    )
    lines = [
        "# Parity Matrix",
        "",
        "Generated by `scripts/gen_parity_matrix.py`. Do not hand-sort rows; "
        "update status/evidence only after recording a TS-reference golden.",
        "",
        "| feature | status | evidence | priority |",
        "|---|---|---|---|",
    ]
    for feature, priority in rows:
        evidence = ALIGNED_EVIDENCE.get(feature)
        status = "aligned" if evidence else "implemented-unverified"
        lines.append(f"| `{feature}` | `{status}` | `{evidence or '-'}` | `{priority}` |")
    return "\n".join(lines) + "\n"


def golden_case_ids() -> set[str]:
    case_ids: set[str] = set()
    for case_path in CASES_DIR.glob("**/case.json"):
        try:
            case = json.loads(case_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid case JSON: {case_path}: {exc}") from exc
        case_id = case.get("case_id")
        if isinstance(case_id, str) and case_id:
            case_ids.add(case_id)
    return case_ids


def check_matrix(matrix_path: Path) -> list[str]:
    if not matrix_path.is_file():
        return [f"Matrix does not exist: {matrix_path}"]

    available_cases = golden_case_ids()
    errors: list[str] = []
    seen_features: set[str] = set()
    for line_number, line in enumerate(matrix_path.read_text(encoding="utf-8").splitlines(), start=1):
        match = TABLE_ROW_RE.match(line)
        if not match:
            continue
        feature = match.group("feature")
        status = match.group("status")
        evidence = match.group("evidence").strip()
        if feature in seen_features:
            errors.append(f"line {line_number}: duplicate feature '{feature}'")
        seen_features.add(feature)
        if status != "aligned":
            continue
        case_ids = [item.strip() for item in evidence.split(",") if item.strip() and item.strip() != "-"]
        if not case_ids:
            errors.append(f"line {line_number}: aligned feature '{feature}' has no golden evidence")
            continue
        for case_id in case_ids:
            if case_id not in available_cases:
                errors.append(
                    f"line {line_number}: aligned feature '{feature}' references missing golden case '{case_id}'"
                )
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate or validate the TS parity matrix.")
    parser.add_argument("--output", type=Path, default=DEFAULT_MATRIX, help="Matrix output path for generation.")
    parser.add_argument("--matrix", type=Path, help="Matrix path to validate (defaults to --output).")
    parser.add_argument("--check", action="store_true", help="Validate aligned rows against recorded golden cases.")
    args = parser.parse_args(argv)

    matrix_path = args.matrix or args.output
    if args.check:
        errors = check_matrix(matrix_path)
        if errors:
            print("Parity matrix validation failed:")
            for error in errors:
                print(f"- {error}")
            return 1
        print(f"Parity matrix validation passed: {matrix_path}")
        return 0

    try:
        matrix_path.parent.mkdir(parents=True, exist_ok=True)
        matrix_path.write_text(render_matrix(), encoding="utf-8")
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 1
    print(f"Wrote parity matrix: {matrix_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
