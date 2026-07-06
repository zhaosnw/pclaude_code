"""
Load agent definitions from agents directories.

Port of: src/tools/AgentTool/loadAgentsDir.ts

Loads user-defined and plugin-provided agent definitions from
.hare/agents/ directories (Markdown frontmatter format).

Parses the full TS BaseAgentDefinition fields from YAML frontmatter.
"""

from __future__ import annotations

import os
import re
import yaml
from typing import Any, Optional

from hare.tools_impl.AgentTool.built_in_agents import (
    AgentDefinition,
    get_builtin_agent_definitions,
)


def load_agents_dir(
    directory: str,
    source: str = "user",
) -> list[AgentDefinition]:
    """Load agent definitions from a directory of markdown files."""
    agents: list[AgentDefinition] = []
    if not os.path.isdir(directory):
        return agents

    for filename in sorted(os.listdir(directory)):
        if not filename.endswith(".md"):
            continue
        filepath = os.path.join(directory, filename)
        try:
            agent = _parse_agent_file(filepath, source)
            if agent:
                agents.append(agent)
        except Exception:
            continue
    return agents


def load_all_agent_definitions(
    project_dir: str,
) -> list[AgentDefinition]:
    """Load all agent definitions from all sources with priority dedup.

    Priority (lowest to highest): built-in → user → project.
    Later definitions with the same agent_type override earlier ones.
    """
    agents = get_builtin_agent_definitions()

    # User agents (~/.hare/agents/)
    user_dir = os.path.join(os.path.expanduser("~"), ".hare", "agents")
    agents.extend(load_agents_dir(user_dir, source="user"))

    # Project agents (.hare/agents/)
    if project_dir:
        project_agents_dir = os.path.join(project_dir, ".hare", "agents")
        agents.extend(load_agents_dir(project_agents_dir, source="project"))

    # Deduplicate by agent_type (later definitions override earlier)
    seen: dict[str, AgentDefinition] = {}
    for agent in agents:
        seen[agent.agent_type] = agent
    return list(seen.values())


def _parse_agent_file(filepath: str, source: str) -> Optional[AgentDefinition]:
    """Parse a Markdown agent definition file with YAML frontmatter.

    TS: parseAgentFromMarkdown — extracts YAML frontmatter for agent config
    and Markdown body for system prompt.

    Supports inline YAML dict format (key: value) and YAML list format (- item).
    """
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    # Extract YAML frontmatter between --- delimiters
    frontmatter_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
    if not frontmatter_match:
        return None

    frontmatter_text = frontmatter_match.group(1)
    body = content[frontmatter_match.end() :]

    # Parse with YAML for proper list/dict handling
    try:
        props: dict[str, Any] = yaml.safe_load(frontmatter_text) or {}
    except (yaml.YAMLError, ImportError):
        # Fallback: simple line-by-line parsing
        props = _parse_frontmatter_plain(frontmatter_text)

    if not isinstance(props, dict):
        return None

    # Resolve agent_type: name > agentType > basename
    agent_type = str(props.get("name", props.get("agentType", "")))
    if not agent_type:
        basename = os.path.basename(filepath)
        agent_type = basename.rsplit(".", 1)[0]

    # Build AgentDefinition with all TS fields
    return AgentDefinition(
        agent_type=agent_type,
        when_to_use=str(props.get("description", props.get("whenToUse", ""))),
        description=str(props.get("description", "")),
        tools=_parse_string_list(props.get("tools", [])),
        disallowed_tools=_parse_string_list(props.get("disallowedTools", [])),
        model=str(props.get("model", "")),
        effort=str(props.get("effort", "")),
        custom_system_prompt=body.strip(),
        source=source,
        mcp_servers=_parse_string_list(props.get("mcpServers", [])),
        skills=_parse_string_list(props.get("skills", [])),
        hooks=props.get("hooks", {}),
        max_turns=int(props.get("maxTurns", 0)),
        permission_mode=str(props.get("permissionMode", "")),
        background=bool(props.get("background", False)),
        color=str(props.get("color", "")),
        omit_claude_md=bool(props.get("omitClaudeMd", False)),
        isolation_mode=str(props.get("isolationMode", "")),
        memory=str(props.get("memory", "")),
    )


def _parse_string_list(value: Any) -> list[str]:
    """Normalize a frontmatter value to a list of strings."""
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str):
        return [value]
    return []


def _parse_frontmatter_plain(text: str) -> dict[str, Any]:
    """Fallback YAML-like parser for when pyyaml is not available.

    Handles:
    - key: value (simple scalars)
    - key: [item1, item2] (inline lists)
    - key:\n  - item1\n  - item2 (YAML block list)
    """
    props: dict[str, Any] = {}
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line or line.startswith("#"):
            i += 1
            continue

        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()

            if not value:
                # Check if followed by indented list items
                items: list[str] = []
                i += 1
                while i < len(lines):
                    next_line = lines[i].rstrip()
                    stripped = next_line.lstrip()
                    if not stripped:
                        i += 1
                        continue
                    if stripped.startswith("- "):
                        items.append(stripped[2:].strip().strip("'\""))
                        i += 1
                    elif next_line and next_line[0] != " " and next_line[0] != "\t":
                        break  # not indented — end of list
                    else:
                        i += 1
                if items:
                    props[key] = items
                continue
            elif value.startswith("[") and value.endswith("]"):
                items = [
                    v.strip().strip("'\"") for v in value[1:-1].split(",") if v.strip()
                ]
                props[key] = items
            elif value.lower() in ("true", "false"):
                props[key] = value.lower() == "true"
            else:
                props[key] = value.strip("'\"")
        i += 1
    return props
