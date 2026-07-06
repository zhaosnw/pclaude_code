"""
Skills directory loading with full frontmatter support.

Port of: src/skills/loadSkillsDir.ts

Loads skill definitions from SKILL.md files with YAML frontmatter parsing.
Supports all TS skill definition fields including paths, hooks, context, etc.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from hare.utils.env_utils import get_hare_config_home_dir


# ---------------------------------------------------------------------------
# LoadedFrom type (TS: src/skills/loadSkillsDir.ts)
# ---------------------------------------------------------------------------

LoadedFrom = (
    str  # "bundled" | "managed" | "skills" | "plugin" | "commands_DEPRECATED" | "mcp"
)


# ---------------------------------------------------------------------------
# SkillDefinition (TS fields)
# ---------------------------------------------------------------------------


@dataclass
class SkillDefinition:
    """A loaded skill definition matching TS skill registration fields."""

    name: str = ""
    path: str = ""
    content: str = ""
    description: str = ""
    when_to_use: str = ""
    source: str = ""  # "bundled" | "user" | "project" | "plugin" | "managed"
    loaded_from: LoadedFrom = "skills"
    # Frontmatter-only fields (from SKILL.md YAML)
    arguments: str = ""
    argument_hint: str = ""
    allowed_tools: list[str] = field(default_factory=list)
    model: str = ""
    effort: str = ""
    context: str = ""  # "fork" | "inline"
    paths: list[str] = field(default_factory=list)  # conditional activation patterns
    user_invocable: bool = True
    disable_model_invocation: bool = False
    agent: str = ""
    hooks: dict[str, Any] = field(default_factory=dict)
    shell: str = ""
    version: str = ""
    enabled: bool = True
    triggers: list[str] = field(default_factory=list)  # legacy compat


# ---------------------------------------------------------------------------
# Directory loading
# ---------------------------------------------------------------------------


def load_skills_dir(
    skills_dir: str,
    source: str = "project",
    loaded_from: LoadedFrom = "skills",
) -> list[SkillDefinition]:
    """Load all skill definitions from a skills directory.

    Each skill is a directory containing a SKILL.md file, or a standalone .md file.
    Supports full YAML frontmatter parsing.
    """
    skills: list[SkillDefinition] = []

    if not os.path.isdir(skills_dir):
        return skills

    # Resolve realpath for dedup (TS: realpath dedup)
    try:
        skills_dir_real = os.path.realpath(skills_dir)
    except OSError:
        skills_dir_real = skills_dir

    seen_realpaths: set[str] = set()

    for entry in sorted(os.listdir(skills_dir)):
        entry_path = os.path.join(skills_dir, entry)

        # Directory with SKILL.md inside (standard format)
        skill_md = os.path.join(entry_path, "SKILL.md")
        if os.path.isdir(entry_path) and os.path.isfile(skill_md):
            skill = _load_skill_file(skill_md, entry, source, loaded_from)
            if skill:
                # Dedup by realpath
                try:
                    rp = os.path.realpath(skill_md)
                    if rp in seen_realpaths:
                        continue
                    seen_realpaths.add(rp)
                except OSError:
                    pass
                skills.append(skill)
            continue

        # Standalone .md file
        if entry.endswith(".md") and os.path.isfile(entry_path):
            name = entry[:-3]
            skill = _load_skill_file(entry_path, name, source, loaded_from)
            if skill:
                skills.append(skill)

    return skills


# ---------------------------------------------------------------------------
# SKILL.md parser with full frontmatter support
# ---------------------------------------------------------------------------


def _load_skill_file(
    path: str,
    name: str,
    source: str,
    loaded_from: LoadedFrom = "skills",
) -> Optional[SkillDefinition]:
    """Parse a SKILL.md file with YAML frontmatter.

    Supports all TS skill definition frontmatter fields, including:
    - name, description, when_to_use
    - arguments, argument-hint
    - allowed-tools (list), model, effort, context
    - paths (conditional activation patterns)
    - user-invocable, disable-model-invocation
    - agent, hooks, shell, version
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return None

    props: dict[str, Any] = {}
    body_content = content
    description = ""
    triggers: list[str] = []

    # Parse YAML frontmatter
    frontmatter_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
    if frontmatter_match:
        frontmatter_text = frontmatter_match.group(1)
        body_content = content[frontmatter_match.end() :]
        props = _parse_yaml_frontmatter(frontmatter_text)

    # Extract description from body if not in frontmatter
    if not props.get("description"):
        for line in body_content.split("\n"):
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                description = stripped
                break

    # Legacy trigger parsing from body (backward compat)
    for line in body_content.split("\n"):
        stripped = line.strip()
        if stripped.startswith("triggers:"):
            triggers_str = stripped[len("triggers:") :].strip()
            triggers = [t.strip() for t in triggers_str.split(",") if t.strip()]

    return SkillDefinition(
        name=props.get("name", name),
        path=path,
        content=body_content.strip(),
        description=props.get("description", description),
        when_to_use=props.get("when_to_use", props.get("whenToUse", "")),
        source=source,
        loaded_from=loaded_from,
        arguments=props.get("arguments", ""),
        argument_hint=props.get("argument_hint", props.get("argument-hint", "")),
        allowed_tools=_parse_string_list(
            props.get("allowed_tools", props.get("allowed-tools", []))
        ),
        model=props.get("model", ""),
        effort=props.get("effort", ""),
        context=props.get("context", ""),
        paths=_parse_string_list(props.get("paths", [])),
        user_invocable=props.get("user_invocable", props.get("user-invocable", True)),
        disable_model_invocation=props.get(
            "disable_model_invocation", props.get("disable-model-invocation", False)
        ),
        agent=props.get("agent", ""),
        hooks=props.get("hooks", {}),
        shell=props.get("shell", ""),
        version=str(props.get("version", "")),
        triggers=triggers,
    )


# ---------------------------------------------------------------------------
# Parameter substitution (TS skill prompt generation)
# ---------------------------------------------------------------------------


def substitute_skill_args(
    content: str,
    args: str = "",
    *,
    skill_dir: str = "",
    session_id: str = "",
    named_args: dict[str, str] | None = None,
) -> str:
    """Substitute skill parameters in prompt content.

    TS parameter substitution order:
    1. Named parameters ($foo → value from args string, indexed by position in arguments: field)
    2. Indexed parameters ($1 → first arg, $ARGUMENTS[0] → first arg)
    3. Full arguments ($ARGUMENTS → complete args string)
    4. Environment variables (${CLAUDE_SKILL_DIR}, ${CLAUDE_SESSION_ID})
    5. If no $ placeholders used, append args text to content
    """
    result = content

    # Environment variable substitution (TS: ${CLAUDE_SKILL_DIR}, ${CLAUDE_SESSION_ID})
    if skill_dir:
        result = result.replace("${CLAUDE_SKILL_DIR}", skill_dir)
        result = result.replace("$CLAUDE_SKILL_DIR", skill_dir)
    if session_id:
        result = result.replace("${CLAUDE_SESSION_ID}", session_id)
        result = result.replace("$CLAUDE_SESSION_ID", session_id)

    if not args:
        if skill_dir:
            prefix = f"Base directory for this skill: {skill_dir}\n\n"
            result = prefix + result
        return result

    arg_parts = args.strip().split()
    has_placeholders = "$" in result

    # 1. Named parameter substitution (TS: named args from frontmatter arguments: field)
    if named_args:
        for key, val in named_args.items():
            result = result.replace(f"${key}", val)

    # 2. Indexed parameter substitution ($1, $2, etc. + $ARGUMENTS[N])
    for i, part in enumerate(arg_parts):
        idx = i + 1
        result = result.replace(f"${idx}", part)
        result = result.replace(f"$ARGUMENTS[{i}]", part)

    # 3. Full arguments
    result = result.replace("$ARGUMENTS", args)

    # 4. If no placeholders were used, append args
    if not has_placeholders:
        result = result.rstrip() + "\n\n" + args

    return result


# ---------------------------------------------------------------------------
# Conditional skill matching (TS: paths frontmatter)
# ---------------------------------------------------------------------------


def _skill_matches_path(skill_content: str, file_path: str) -> bool:
    """Check if a skill's paths patterns match a file path.

    TS: uses ignore (gitignore-style) matching. Simplified version uses
    glob-style matching against the paths frontmatter.
    """
    # Extract paths from frontmatter if present
    frontmatter_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", skill_content, re.DOTALL)
    if not frontmatter_match:
        # Legacy: check against content
        basename = os.path.basename(file_path)
        _, ext = os.path.splitext(file_path)
        patterns = [basename, f"*{ext}", file_path]
        for pattern in patterns:
            if pattern in skill_content:
                return True
        return False

    frontmatter_text = frontmatter_match.group(1)
    props = _parse_yaml_frontmatter(frontmatter_text)
    paths = _parse_string_list(props.get("paths", []))

    if not paths:
        return False

    import fnmatch

    for pattern in paths:
        if fnmatch.fnmatch(file_path, pattern):
            return True
    return False


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def get_project_skills_dir(cwd: str) -> str:
    """Get the project-level skills directory."""
    return os.path.join(cwd, ".hare", "skills")


def get_user_skills_dir() -> str:
    """Get the user-level skills directory."""
    return os.path.join(get_hare_config_home_dir(), "skills")


def get_managed_skills_dir() -> str:
    """Get managed/policy skills directory."""
    import sys

    if sys.platform == "darwin":
        base = "/Library/Application Support/HareCode"
    elif sys.platform == "win32":
        base = os.path.join(
            os.environ.get("PROGRAMDATA", "C:\\ProgramData"), "HareCode"
        )
    else:
        base = "/etc/hare-code"
    return os.path.join(base, "skills")


# ---------------------------------------------------------------------------
# Full loading (5 sources + dedup)
# ---------------------------------------------------------------------------


def load_all_skills(cwd: str) -> list[SkillDefinition]:
    """Load skills from all sources with priority dedup.

    TS: getSkillDirCommands — loads from 5 sources, dedup by realpath.
    Priority (lowest to highest): bundled → user → project → additional → managed.
    """
    skills: list[SkillDefinition] = []
    seen_names: dict[str, SkillDefinition] = {}

    # User-level skills (lowest priority)
    user_dir = get_user_skills_dir()
    for skill in load_skills_dir(user_dir, "user", "skills"):
        seen_names[skill.name] = skill

    # Project-level skills
    project_dir = get_project_skills_dir(cwd)
    for skill in load_skills_dir(project_dir, "project", "skills"):
        seen_names[skill.name] = skill  # overwrites user

    # Managed/policy skills (highest priority)
    managed_dir = get_managed_skills_dir()
    if not os.environ.get("CLAUDE_CODE_DISABLE_POLICY_SKILLS"):
        for skill in load_skills_dir(managed_dir, "managed", "managed"):
            seen_names[skill.name] = skill  # overwrites all

    return list(seen_names.values())


# ---------------------------------------------------------------------------
# Dynamic discovery and conditional activation
# ---------------------------------------------------------------------------


def discover_skill_dirs_for_paths(paths: list[str], cwd: str) -> list[str]:
    """Given file paths, discover skill directories with conditional triggers."""
    skill_dirs: list[str] = []
    project_skills = get_project_skills_dir(cwd)
    user_skills = get_user_skills_dir()

    for skills_base in (project_skills, user_skills):
        if not os.path.isdir(skills_base):
            continue
        for entry in sorted(os.listdir(skills_base)):
            skill_dir = os.path.join(skills_base, entry)
            if not os.path.isdir(skill_dir):
                continue
            skill_md = os.path.join(skill_dir, "SKILL.md")
            if not os.path.isfile(skill_md):
                continue
            try:
                with open(skill_md, "r", encoding="utf-8") as f:
                    content = f.read()
                for path in paths:
                    if _skill_matches_path(content, path):
                        skill_dirs.append(skill_dir)
                        break
            except OSError:
                pass

    return skill_dirs


def activate_conditional_skills_for_paths(
    paths: list[str],
    cwd: str,
    discovered_skill_names: set[str] | None = None,
) -> list[str]:
    """Discover and activate conditional skills triggered by file paths."""
    skill_dirs = discover_skill_dirs_for_paths(paths, cwd)
    activated: list[str] = []

    for skill_dir in skill_dirs:
        skill_md = os.path.join(skill_dir, "SKILL.md")
        try:
            skill = _load_skill_file(
                skill_md, os.path.basename(skill_dir), "conditional"
            )
            if skill and skill.name not in (discovered_skill_names or set()):
                if discovered_skill_names is not None:
                    discovered_skill_names.add(skill.name)
                activated.append(skill.name)
        except Exception:
            pass

    return activated


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_yaml_frontmatter(text: str) -> dict[str, Any]:
    """Parse YAML frontmatter into a dict.

    Handles:
    - key: value (scalars, with auto-type detection)
    - key: [item1, item2] (inline list)
    - key:
        - item1
        - item2 (block list)
    """
    props: dict[str, Any] = {}
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        if not line or line.startswith("#"):
            i += 1
            continue

        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()

            if not value:
                # Potential block list
                items: list[str] = []
                j = i + 1
                while j < len(lines):
                    nl = lines[j].rstrip()
                    if not nl:
                        j += 1
                        continue
                    stripped = nl.lstrip()
                    if stripped.startswith("- "):
                        items.append(stripped[2:].strip().strip("'\""))
                        j += 1
                    elif nl and nl[0] not in (" ", "\t"):
                        break
                    elif nl.lstrip().startswith("- "):
                        items.append(nl.lstrip()[2:].strip().strip("'\""))
                        j += 1
                    else:
                        j += 1
                if items:
                    props[key] = items
                    i = j
                    continue
                else:
                    # Empty value, skip
                    i += 1
                    continue
            elif value.startswith("[") and value.endswith("]"):
                items = [
                    v.strip().strip("'\"") for v in value[1:-1].split(",") if v.strip()
                ]
                props[key] = items
            elif value.lower() in ("true", "false"):
                props[key] = value.lower() == "true"
            elif value.isdigit():
                props[key] = int(value)
            else:
                props[key] = value.strip("'\"")
        i += 1

    return props


def _parse_string_list(value: Any) -> list[str]:
    """Normalize a frontmatter value to a list of strings."""
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str) and value:
        return [value]
    return []
