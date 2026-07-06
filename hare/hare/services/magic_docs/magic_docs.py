"""
MagicDocs — automated documentation generation for tools, skills, and components.

Port of: src/services/MagicDocs/

Generates structured documentation from:
- Tool schemas (input/output parameters, descriptions, permissions)
- Skill definitions (name, description, when-to-use, content previews)
- Component docstrings and module-level overviews

Produces Markdown and HTML output suitable for rendering in the UI or
exporting as static reference docs.
"""

from __future__ import annotations

import importlib
import inspect
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

# ---------------------------------------------------------------------------
# Documentation data models
# ---------------------------------------------------------------------------


@dataclass
class MagicDocsResult:
    """Search result container returned by search_docs()."""

    query: str = ""
    results: list[dict[str, str]] = field(default_factory=list)
    total_hits: int = 0
    searched_tools: int = 0
    searched_skills: int = 0
    searched_modules: int = 0


@dataclass
class ParameterDoc:
    """Documentation for a single tool/skill parameter."""

    name: str = ""
    type: str = ""
    description: str = ""
    required: bool = False
    default: str = ""
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    enum_values: list[str] = field(default_factory=list)


@dataclass
class ToolDoc:
    """Structured documentation for a single tool."""

    name: str = ""
    aliases: list[str] = field(default_factory=list)
    description: str = ""
    search_hint: str = ""
    read_only: bool = False
    destructive: bool = False
    concurrency_safe: bool = False
    parameters: list[ParameterDoc] = field(default_factory=list)
    output_schema: dict[str, Any] | None = None
    usage_example: str = ""
    module_path: str = ""
    source_summary: str = ""


@dataclass
class SkillDoc:
    """Structured documentation for a single skill."""

    name: str = ""
    description: str = ""
    when_to_use: str = ""
    source: str = "user"
    skill_type: str = "prompt"
    content_preview: str = ""
    content_length: int = 0
    path: str = ""
    enabled: bool = True


@dataclass
class ModuleDoc:
    """Structured documentation for a Python module."""

    name: str = ""
    path: str = ""
    summary: str = ""
    classes: list[str] = field(default_factory=list)
    functions: list[str] = field(default_factory=list)
    docstring: str = ""


@dataclass
class GeneratedDocs:
    """Container for all generated documentation."""

    tools: list[ToolDoc] = field(default_factory=list)
    skills: list[SkillDoc] = field(default_factory=list)
    modules: list[ModuleDoc] = field(default_factory=list)
    generation_notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Tool documentation generation
# ---------------------------------------------------------------------------


def _schema_to_parameters(schema: dict[str, Any]) -> list[ParameterDoc]:
    """Convert a JSON Schema object properties dict into ParameterDoc list."""
    params: list[ParameterDoc] = []
    if not schema or not isinstance(schema, dict):
        return params

    properties = schema.get("properties", {})
    required = set(schema.get("required", []))
    if not properties or not isinstance(properties, dict):
        return params

    for name, prop in properties.items():
        if not isinstance(prop, dict):
            continue
        ptype = prop.get("type", "any")
        if isinstance(ptype, list):
            ptype = " | ".join(str(t) for t in ptype)

        enum_vals: list[str] = []
        if isinstance(prop.get("enum"), list):
            enum_vals = [str(v) for v in prop["enum"]]

        params.append(
            ParameterDoc(
                name=name,
                type=ptype,
                description=prop.get("description", ""),
                required=name in required,
                default=str(prop["default"]) if "default" in prop else "",
                minimum=prop.get("minimum"),
                maximum=prop.get("maximum"),
                enum_values=enum_vals,
            )
        )

    return params


def generate_tool_doc_single(tool: Any, *, module_path: str = "") -> ToolDoc:
    """Generate structured documentation for a single tool instance.

    Introspects the tool's name, aliases, schema, and permissions metadata
    to produce a comprehensive ToolDoc.

    Args:
        tool: A Tool instance (implements the Tool Protocol).
        module_path: Optional path to the tool's implementation module.

    Returns:
        ToolDoc with full parameter and behavioral documentation.
    """
    doc = ToolDoc()

    # Basic identity
    doc.name = getattr(tool, "name", "")
    doc.aliases = list(getattr(tool, "aliases", []) or [])
    doc.search_hint = getattr(tool, "search_hint", "")

    # Behavioral flags
    doc.read_only = bool(getattr(tool, "is_read_only", lambda _: False)({}))
    doc.destructive = bool(getattr(tool, "is_destructive", lambda _: False)({}))
    doc.concurrency_safe = bool(
        getattr(tool, "is_concurrency_safe", lambda _: False)({})
    )

    # Input schema → parameters
    try:
        input_schema = tool.input_schema()
        doc.parameters = _schema_to_parameters(input_schema) if input_schema else []
    except Exception:
        doc.parameters = []

    # Output schema
    try:
        out_fn = getattr(tool, "output_schema", None)
        if callable(out_fn):
            doc.output_schema = out_fn()
    except Exception:
        doc.output_schema = None

    # Module path
    if module_path:
        doc.module_path = module_path
    elif hasattr(tool, "__class__"):
        cls = tool.__class__
        doc.module_path = f"{cls.__module__}.{cls.__qualname__}"

    # Source summary from the implementation module
    doc.source_summary = _extract_module_summary(tool)

    # Build usage example from parameter names
    doc.usage_example = _build_tool_usage_example(doc)

    # Description
    doc.description = _build_tool_description(doc)

    return doc


def _extract_module_summary(tool: Any) -> str:
    """Extract a source summary from the tool's implementation module docstring."""
    cls = tool.__class__
    module = inspect.getmodule(cls)
    if module is not None:
        docstring = inspect.getdoc(module) or ""
        if docstring:
            first_line = docstring.strip().split("\n")[0].strip()
            return first_line
    return ""


def _build_tool_usage_example(doc: ToolDoc) -> str:
    """Build a minimal usage example from parameter names."""
    if not doc.parameters:
        return f'{{}}  # {doc.name}()'

    example_parts: list[str] = []
    for p in doc.parameters:
        if p.required:
            if p.type == "string":
                example_parts.append(f'  "{p.name}": "..."')
            elif p.type in ("integer", "number"):
                val = str(int(p.minimum)) if p.minimum is not None else "0"
                example_parts.append(f'  "{p.name}": {val}')
            elif p.type == "boolean":
                example_parts.append(f'  "{p.name}": false')
            elif p.type == "array":
                example_parts.append(f'  "{p.name}": []')
            else:
                example_parts.append(f'  "{p.name}": ...')

    if example_parts:
        return "{\n" + ",\n".join(example_parts) + "\n}"
    else:
        return "{}"


def _build_tool_description(doc: ToolDoc) -> str:
    """Build a human-readable description from available metadata."""
    parts: list[str] = []

    if doc.search_hint:
        parts.append(doc.search_hint.capitalize())

    behaviors: list[str] = []
    if doc.read_only:
        behaviors.append("read-only")
    if doc.destructive:
        behaviors.append("destructive")
    if doc.concurrency_safe:
        behaviors.append("concurrency-safe")
    if behaviors:
        parts.append(f"[{' | '.join(behaviors)}]")

    # Count parameters
    required_count = sum(1 for p in doc.parameters if p.required)
    opt_count = len(doc.parameters) - required_count
    if doc.parameters:
        parts.append(
            f"{len(doc.parameters)} params ({required_count} required, {opt_count} optional)"
        )

    if doc.aliases:
        parts.append(f"aliases: {', '.join(doc.aliases)}")

    return " — ".join(parts) if parts else doc.name


def generate_tool_docs(
    tools: list[Any] | None = None,
    *,
    include_disabled: bool = False,
) -> list[ToolDoc]:
    """Generate documentation for all registered tools.

    Uses the tool registry (get_all_base_tools) by default; accepts an
    explicit tool list for testing or custom subsets.

    Args:
        tools: Optional list of Tool instances. If None, auto-discovers from registry.
        include_disabled: If True, include tools even when is_enabled() returns False.

    Returns:
        List of ToolDoc instances, one per tool.
    """
    if tools is None:
        try:
            from hare.tools import get_all_base_tools as _all_tools

            tools = list(_all_tools())
        except ImportError:
            tools = []

    docs: list[ToolDoc] = []
    for tool in tools:
        if not include_disabled and not tool.is_enabled():
            continue

        # Determine module path
        module_path = ""
        if hasattr(tool, "__class__"):
            cls = tool.__class__
            module_path = f"{cls.__module__}.{cls.__qualname__}"

        try:
            doc = generate_tool_doc_single(tool, module_path=module_path)
            docs.append(doc)
        except Exception:
            pass

    return sorted(docs, key=lambda d: d.name.lower())


# ---------------------------------------------------------------------------
# Skill documentation generation
# ---------------------------------------------------------------------------


def generate_skill_docs(
    skills_dirs: list[str] | None = None,
    *,
    include_disabled: bool = True,
) -> list[SkillDoc]:
    """Generate documentation for all discoverable skills.

    Scans skill directories (CLAUDE.md skill dirs, bundled skills, user skills)
    and produces structured SkillDoc entries.

    Args:
        skills_dirs: Optional list of directories to scan for skills.
        include_disabled: If False, exclude disabled skills.

    Returns:
        List of SkillDoc instances.
    """
    docs: list[SkillDoc] = []

    # Load bundled skills (BundledSkill dataclass)
    try:
        from hare.skills.bundled import get_all_bundled_skills

        bundled = get_all_bundled_skills()
        for skill in bundled:
            # BundledSkill is a dataclass; use getattr with safe fallback.
            name = _safe_attr(skill, "name", "")
            if not name:
                continue
            if not include_disabled and not _safe_attr(skill, "enabled", True):
                continue
            content = _safe_attr(skill, "content", "")
            docs.append(
                SkillDoc(
                    name=name,
                    description=_safe_attr(skill, "description", ""),
                    when_to_use=_safe_attr(skill, "when_to_use", _safe_attr(skill, "whenToUse", "")),
                    source="bundled",
                    skill_type=_safe_attr(skill, "type", "prompt"),
                    content_preview=_truncate_content(content),
                    content_length=len(content),
                    path=_safe_attr(skill, "path", ""),
                    enabled=_safe_attr(skill, "enabled", True),
                )
            )
    except ImportError:
        pass

    # Load skills from file-system directories (SkillDefinition dataclass)
    if skills_dirs is not None:
        try:
            from hare.skills.loader import load_skills_dir

            for sdir in skills_dirs:
                if not os.path.isdir(sdir):
                    continue
                try:
                    loaded = load_skills_dir(sdir)
                    for skill_def in loaded:
                        content = _safe_attr(skill_def, "content", "")
                        docs.append(
                            SkillDoc(
                                name=_safe_attr(skill_def, "name", ""),
                                description=_safe_attr(skill_def, "description", ""),
                                when_to_use=_safe_attr(skill_def, "when_to_use", ""),
                                source=_safe_attr(skill_def, "source", "user"),
                                skill_type=_safe_attr(skill_def, "type", _safe_attr(skill_def, "skill_type", "prompt")),
                                content_preview=_truncate_content(content),
                                content_length=len(content),
                                path=_safe_attr(skill_def, "path", ""),
                                enabled=_safe_attr(skill_def, "enabled", True),
                            )
                        )
                except Exception:
                    pass
        except ImportError:
            pass

    return sorted(docs, key=lambda d: d.name.lower())


def _safe_attr(obj: Any, attr: str, default: Any = "") -> Any:
    """Safely get an attribute from an object (dataclass or dict).

    Handles both attribute access (dataclass) and key access (dict) so
    the docs generator works with either representation.
    """
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(attr, default)
    return getattr(obj, attr, default)


def _truncate_content(content: str, max_chars: int = 200) -> str:
    """Truncate content for preview display."""
    if not content:
        return ""
    trimmed = content.strip()
    if len(trimmed) <= max_chars:
        return trimmed
    return trimmed[: max_chars - 3] + "..."


# ---------------------------------------------------------------------------
# Module documentation generation
# ---------------------------------------------------------------------------


def generate_module_docs(
    module_paths: list[str] | None = None,
    *,
    base_package: str = "hare",
    max_depth: int = 2,
) -> list[ModuleDoc]:
    """Generate documentation for Python modules in the project.

    Walks the package tree and extracts docstrings, class names, and function
    names for each discoverable module.

    Args:
        module_paths: Specific module paths to document. If None, auto-discovers
                      from the hare package.
        base_package: Root package name to walk.
        max_depth: Maximum depth of submodule recursion.

    Returns:
        List of ModuleDoc instances.
    """
    docs: list[ModuleDoc] = []

    if module_paths is None:
        try:
            base = importlib.import_module(base_package)
            module_paths = _discover_modules(base, base_package, max_depth)
        except ImportError:
            module_paths = []

    for mp in module_paths:
        try:
            mod = importlib.import_module(mp)
        except (ImportError, ModuleNotFoundError):
            continue

        docstring = inspect.getdoc(mod) or ""
        summary = docstring.strip().split("\n")[0].strip() if docstring else ""

        classes: list[str] = []
        functions: list[str] = []
        for name, obj in inspect.getmembers(mod):
            if inspect.isclass(obj) and obj.__module__ == mp:
                classes.append(name)
            elif inspect.isfunction(obj) and obj.__module__ == mp:
                if not name.startswith("_"):
                    functions.append(name)

        file_path = ""
        if hasattr(mod, "__file__") and mod.__file__:
            file_path = mod.__file__

        docs.append(
            ModuleDoc(
                name=mp,
                path=file_path,
                summary=summary,
                classes=sorted(classes),
                functions=sorted(functions),
                docstring=docstring or "",
            )
        )

    return sorted(docs, key=lambda d: d.name.lower())


def _discover_modules(
    package: Any, package_name: str, max_depth: int
) -> list[str]:
    """Recursively discover submodules of a package."""
    modules: list[str] = []
    if max_depth <= 0:
        return modules

    pkg_path: str | None = None
    if hasattr(package, "__path__"):
        paths = package.__path__
        if isinstance(paths, list) and paths:
            pkg_path = paths[0]

    if not pkg_path or not os.path.isdir(pkg_path):
        return modules

    try:
        entries = os.listdir(pkg_path)
    except OSError:
        return modules

    for entry in sorted(entries):
        if entry.startswith("_") or entry.startswith("."):
            continue

        full_path = os.path.join(pkg_path, entry)
        if os.path.isdir(full_path):
            # Sub-package
            init = os.path.join(full_path, "__init__.py")
            if os.path.isfile(init):
                sub_name = f"{package_name}.{entry}"
                modules.append(sub_name)
                try:
                    sub_pkg = importlib.import_module(sub_name)
                    modules.extend(
                        _discover_modules(sub_pkg, sub_name, max_depth - 1)
                    )
                except ImportError:
                    pass
        elif entry.endswith(".py") and entry != "__init__.py":
            mod_name = f"{package_name}.{entry[:-3]}"
            modules.append(mod_name)

    return modules


# ---------------------------------------------------------------------------
# Comprehensive generation
# ---------------------------------------------------------------------------


def generate_all_docs(
    *,
    tools: list[Any] | None = None,
    skills_dirs: list[str] | None = None,
    module_paths: list[str] | None = None,
    include_disabled_tools: bool = False,
    include_disabled_skills: bool = True,
    base_package: str = "hare",
    max_module_depth: int = 2,
) -> GeneratedDocs:
    """Generate comprehensive documentation for tools, skills, and modules.

    This is the primary entry point for batch documentation generation.

    Args:
        tools: Tool instances. Auto-discovers if None.
        skills_dirs: Skill directories to scan. Uses empty list if None.
        module_paths: Specific module paths. Auto-discovers if None.
        include_disabled_tools: Include tools where is_enabled() is False.
        include_disabled_skills: Include disabled skills.
        base_package: Root package for module discovery.
        max_module_depth: Max depth for module discovery.

    Returns:
        GeneratedDocs containing all generated documentation.
    """
    notes: list[str] = []

    tool_docs = generate_tool_docs(
        tools=tools,
        include_disabled=include_disabled_tools,
    )
    notes.append(f"Generated docs for {len(tool_docs)} tools.")

    skill_docs = generate_skill_docs(
        skills_dirs=skills_dirs,
        include_disabled=include_disabled_skills,
    )
    notes.append(f"Generated docs for {len(skill_docs)} skills.")

    module_docs = generate_module_docs(
        module_paths=module_paths,
        base_package=base_package,
        max_depth=max_module_depth,
    )
    notes.append(f"Generated docs for {len(module_docs)} modules.")

    return GeneratedDocs(
        tools=tool_docs,
        skills=skill_docs,
        modules=module_docs,
        generation_notes=notes,
    )


# ---------------------------------------------------------------------------
# Search functionality
# ---------------------------------------------------------------------------


def search_docs(
    query: str,
    *,
    context: str = "",
    tools: list[Any] | None = None,
    skills_dirs: list[str] | None = None,
    max_results: int = 20,
) -> MagicDocsResult:
    """Search across all documentation for matching terms.

    Performs case-insensitive substring matching across tool names,
    tool descriptions, parameter names, skill names, skill descriptions,
    and module summaries.

    Args:
        query: The search query string.
        context: Optional context hint (e.g., 'tool', 'skill', 'module') to
                 scope the search.
        tools: Optional tool list. Auto-discovers if None.
        skills_dirs: Optional skill directories.
        max_results: Maximum number of results to return.

    Returns:
        MagicDocsResult with ranked search results.
    """
    query_lower = query.lower().strip()
    if not query_lower:
        return MagicDocsResult(query=query)

    results: list[dict[str, str]] = []

    # Generate all docs for searching
    all_tool_docs = generate_tool_docs(tools=tools, include_disabled=True)
    all_skill_docs = generate_skill_docs(skills_dirs=skills_dirs)

    # Search tools
    if not context or context == "tool":
        for td in all_tool_docs:
            score = _score_tool_match(td, query_lower)
            if score > 0:
                results.append(
                    {
                        "type": "tool",
                        "name": td.name,
                        "description": td.description,
                        "match_score": str(score),
                        "snippet": _build_tool_snippet(td, query_lower),
                    }
                )

    # Search skills
    if not context or context == "skill":
        for sd in all_skill_docs:
            score = _score_skill_match(sd, query_lower)
            if score > 0:
                results.append(
                    {
                        "type": "skill",
                        "name": sd.name,
                        "description": sd.description,
                        "match_score": str(score),
                        "snippet": sd.content_preview[:300],
                    }
                )

    # Search modules
    if not context or context == "module":
        try:
            module_docs = generate_module_docs(module_paths=None)
        except Exception:
            module_docs = []
        for md in module_docs:
            score = _score_module_match(md, query_lower)
            if score > 0:
                results.append(
                    {
                        "type": "module",
                        "name": md.name,
                        "description": md.summary,
                        "match_score": str(score),
                        "snippet": md.docstring[:300],
                    }
                )

    # Sort by score (descending)
    results.sort(key=lambda r: int(r.get("match_score", "0")), reverse=True)

    return MagicDocsResult(
        query=query,
        results=results[:max_results],
        total_hits=len(results),
        searched_tools=len(all_tool_docs),
        searched_skills=len(all_skill_docs),
        searched_modules=len(
            [r for r in results if r.get("type") == "module"]
        ),
    )


def _score_tool_match(doc: ToolDoc, query: str) -> int:
    """Score a tool doc against a query. Returns 0 for no match."""
    score = 0
    if query in doc.name.lower():
        score += 10
    if any(query in a.lower() for a in doc.aliases):
        score += 8
    if query in doc.search_hint.lower():
        score += 5
    if query in doc.description.lower():
        score += 3
    for p in doc.parameters:
        if query in p.name.lower():
            score += 2
        if query in p.description.lower():
            score += 1
    return score


def _score_skill_match(doc: SkillDoc, query: str) -> int:
    """Score a skill doc against a query."""
    score = 0
    if query in doc.name.lower():
        score += 10
    if query in doc.description.lower():
        score += 5
    if query in doc.when_to_use.lower():
        score += 3
    if query in doc.content_preview.lower():
        score += 2
    return score


def _score_module_match(doc: ModuleDoc, query: str) -> int:
    """Score a module doc against a query."""
    score = 0
    if query in doc.name.lower():
        score += 10
    if query in doc.summary.lower():
        score += 5
    if any(query in c.lower() for c in doc.classes):
        score += 3
    if any(query in f.lower() for f in doc.functions):
        score += 2
    return score


def _build_tool_snippet(doc: ToolDoc, query: str) -> str:
    """Build a search result snippet for a tool, highlighting the matched term."""
    if query in doc.name.lower():
        return f"Tool: {doc.name} — {doc.search_hint}"
    if any(query in a.lower() for a in doc.aliases):
        return f"Tool: {doc.name} (aliases: {', '.join(doc.aliases)})"
    if query in doc.description.lower():
        return f"Tool: {doc.name} — {doc.description[:200]}"
    for p in doc.parameters:
        if query in p.name.lower():
            return f"Tool: {doc.name} → parameter '{p.name}': {p.description[:150]}"
        if query in p.description.lower():
            return f"Tool: {doc.name} → {p.name}: {p.description[:150]}"
    return f"Tool: {doc.name}"


# ---------------------------------------------------------------------------
# Markdown formatting
# ---------------------------------------------------------------------------


def format_tool_doc_as_markdown(doc: ToolDoc) -> str:
    """Format a single ToolDoc as a Markdown section."""
    lines: list[str] = []

    lines.append(f"### `{doc.name}`")
    lines.append("")
    lines.append(doc.description)
    lines.append("")

    # Behavior badges
    badges: list[str] = []
    if doc.read_only:
        badges.append("read-only")
    if doc.destructive:
        badges.append("destructive")
    if doc.concurrency_safe:
        badges.append("concurrency-safe")
    if badges:
        lines.append("**Behavior:** " + " | ".join(badges))
        lines.append("")

    # Aliases
    if doc.aliases:
        lines.append(f"**Aliases:** {', '.join(f'`{a}`' for a in doc.aliases)}")
        lines.append("")

    # Parameters table
    if doc.parameters:
        lines.append("**Parameters:**")
        lines.append("")
        lines.append("| Name | Type | Required | Description |")
        lines.append("|------|------|----------|-------------|")
        for p in doc.parameters:
            req = "Yes" if p.required else "No"
            desc = p.description
            if p.enum_values:
                desc += f" (enum: {', '.join(p.enum_values)})"
            if p.minimum is not None:
                desc += f" (min: {p.minimum})"
            if p.maximum is not None:
                desc += f" (max: {p.maximum})"
            lines.append(f"| `{p.name}` | {p.type} | {req} | {desc} |")
        lines.append("")

    # Usage example
    if doc.usage_example:
        lines.append("**Usage:**")
        lines.append("")
        lines.append("```json")
        lines.append(doc.usage_example)
        lines.append("```")
        lines.append("")

    # Output schema
    if doc.output_schema:
        lines.append("**Output Schema:**")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(doc.output_schema, indent=2))
        lines.append("```")
        lines.append("")

    return "\n".join(lines)


def format_skill_doc_as_markdown(doc: SkillDoc) -> str:
    """Format a single SkillDoc as a Markdown section."""
    lines: list[str] = []

    lines.append(f"### {doc.name}")
    lines.append("")

    if doc.description:
        lines.append(doc.description)
        lines.append("")

    # Metadata
    meta: list[str] = []
    meta.append(f"**Source:** {doc.source}")
    meta.append(f"**Type:** {doc.skill_type}")
    if doc.when_to_use:
        meta.append(f"**When to use:** {doc.when_to_use}")
    meta.append(f"**Content length:** {doc.content_length} chars")
    if doc.path:
        meta.append(f"**Path:** `{doc.path}`")
    lines.append(" | ".join(meta))
    lines.append("")

    # Content preview
    if doc.content_preview:
        lines.append("**Content preview:**")
        lines.append("")
        lines.append("```markdown")
        lines.append(doc.content_preview)
        lines.append("```")
        lines.append("")

    return "\n".join(lines)


def format_module_doc_as_markdown(doc: ModuleDoc) -> str:
    """Format a single ModuleDoc as a Markdown section."""
    lines: list[str] = []

    lines.append(f"### `{doc.name}`")
    lines.append("")

    if doc.summary:
        lines.append(doc.summary)
        lines.append("")

    # Classes and functions
    if doc.classes:
        lines.append("**Classes:** " + ", ".join(f"`{c}`" for c in doc.classes))
        lines.append("")

    if doc.functions:
        func_list = ", ".join(f"`{f}()`" for f in doc.functions)
        lines.append(f"**Functions:** {func_list}")
        lines.append("")

    return "\n".join(lines)


def format_as_markdown(docs: GeneratedDocs) -> str:
    """Render a full GeneratedDocs container as a Markdown document.

    Produces a complete reference document with table of contents, tool
    reference, skill reference, and module overview sections.
    """
    lines: list[str] = []

    # Title and header
    lines.append("# Hare Documentation Reference")
    lines.append("")
    lines.append("> Auto-generated by MagicDocs")
    lines.append("")

    # Table of contents
    lines.append("## Table of Contents")
    lines.append("")
    if docs.tools:
        lines.append("- [Tools](#tools)")
    if docs.skills:
        lines.append("- [Skills](#skills)")
    if docs.modules:
        lines.append("- [Modules](#modules)")
    lines.append("")

    # Tools section
    if docs.tools:
        lines.append("## Tools")
        lines.append("")
        lines.append(f"*{len(docs.tools)} tools documented*")
        lines.append("")
        for tool_doc in docs.tools:
            lines.append(format_tool_doc_as_markdown(tool_doc))
            lines.append("---")
            lines.append("")

    # Skills section
    if docs.skills:
        lines.append("## Skills")
        lines.append("")
        lines.append(f"*{len(docs.skills)} skills documented*")
        lines.append("")
        for skill_doc in docs.skills:
            lines.append(format_skill_doc_as_markdown(skill_doc))
            lines.append("---")
            lines.append("")

    # Modules section
    if docs.modules:
        lines.append("## Modules")
        lines.append("")
        lines.append(f"*{len(docs.modules)} modules documented*")
        lines.append("")
        for module_doc in docs.modules:
            lines.append(format_module_doc_as_markdown(module_doc))
            lines.append("")

    # Footer
    lines.append("---")
    lines.append("")
    notes = docs.generation_notes or []
    for note in notes:
        lines.append(f"*{note}*")
        lines.append("")

    return "\n".join(lines)


def format_as_html(docs: GeneratedDocs) -> str:
    """Render a full GeneratedDocs container as an HTML document.

    Produces a standalone HTML page with embedded CSS styling.
    """
    import textwrap

    # Use the markdown version as content, wrapping in HTML
    md_content = format_as_markdown(docs)

    # Simple markdown-to-HTML conversion for common elements
    html_body = _markdown_to_html(md_content)

    return textwrap.dedent(f"""\
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Hare Documentation Reference</title>
        <style>
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                max-width: 960px;
                margin: 0 auto;
                padding: 2rem;
                line-height: 1.6;
                color: #1a1a1a;
                background: #ffffff;
            }}
            h1 {{ font-size: 2rem; border-bottom: 2px solid #e5e5e5; padding-bottom: 0.5rem; }}
            h2 {{ font-size: 1.5rem; margin-top: 2rem; border-bottom: 1px solid #e5e5e5; padding-bottom: 0.25rem; }}
            h3 {{ font-size: 1.2rem; margin-top: 1.5rem; }}
            code {{ background: #f5f5f5; padding: 0.15em 0.4em; border-radius: 3px; font-size: 0.9em; }}
            pre {{ background: #f5f5f5; padding: 1rem; border-radius: 6px; overflow-x: auto; }}
            table {{ border-collapse: collapse; width: 100%; margin: 1rem 0; }}
            th, td {{ border: 1px solid #ddd; padding: 0.5rem 0.75rem; text-align: left; }}
            th {{ background: #f5f5f5; font-weight: 600; }}
            hr {{ border: none; border-top: 2px solid #e5e5e5; margin: 2rem 0; }}
            blockquote {{ border-left: 3px solid #e5e5e5; padding-left: 1rem; color: #666; margin: 1rem 0; }}
            a {{ color: #1a56db; }}
        </style>
    </head>
    <body>
    {html_body}
    </body>
    </html>""")


def _markdown_to_html(md: str) -> str:
    """Convert basic Markdown to HTML.

    A minimal converter that handles h1-h3, code blocks, inline code,
    tables, horizontal rules, and paragraphs. For full rendering, use an
    external Markdown library.
    """
    lines = md.split("\n")
    output: list[str] = []
    i = 0

    in_code_block = False
    in_table = False
    table_header_rendered = False

    while i < len(lines):
        line = lines[i]

        # Code blocks (fenced)
        if line.strip().startswith("```"):
            if in_code_block:
                output.append("</code></pre>")
                in_code_block = False
            else:
                lang = line.strip()[3:].strip()
                output.append(f'<pre><code class="language-{lang}">')
                in_code_block = True
            i += 1
            continue

        if in_code_block:
            output.append(_escape_html(line))
            i += 1
            continue

        # Horizontal rule
        if line.strip() == "---":
            output.append("<hr>")
            if in_table:
                output.append("</table>")
                in_table = False
                table_header_rendered = False
            i += 1
            continue

        # Table row
        if "|" in line and line.strip().startswith("|"):
            cells = [c.strip() for c in line.split("|")[1:-1]]
            if not in_table:
                output.append("<table>")
                in_table = True
                table_header_rendered = False
            if all(c.strip() == c.strip().lstrip("-").rstrip("-") for c in cells if c):
                # Separator row
                i += 1
                continue
            if not table_header_rendered:
                output.append("<thead><tr>")
                for cell in cells:
                    output.append(f"<th>{_inline_md_to_html(cell)}</th>")
                output.append("</tr></thead><tbody>")
                table_header_rendered = True
            else:
                output.append("<tr>")
                for cell in cells:
                    output.append(f"<td>{_inline_md_to_html(cell)}</td>")
                output.append("</tr>")
            i += 1
            continue
        elif in_table:
            output.append("</table>")
            in_table = False
            table_header_rendered = False

        # Headings
        if line.startswith("### "):
            output.append(f"<h3>{_inline_md_to_html(line[4:])}</h3>")
            i += 1
            continue
        if line.startswith("## "):
            output.append(f"<h2>{_inline_md_to_html(line[3:])}</h2>")
            i += 1
            continue
        if line.startswith("# "):
            output.append(f"<h1>{_inline_md_to_html(line[2:])}</h1>")
            i += 1
            continue

        # Blockquote
        if line.startswith("> "):
            output.append(f"<blockquote>{_inline_md_to_html(line[2:])}</blockquote>")
            i += 1
            continue

        # Empty line
        if not line.strip():
            output.append("<br>")
            i += 1
            continue

        # Regular paragraph
        output.append(f"<p>{_inline_md_to_html(line)}</p>")
        i += 1

    # Close any open tags
    if in_table:
        output.append("</table>")
    if in_code_block:
        output.append("</code></pre>")

    return "\n".join(output)


def _escape_html(text: str) -> str:
    """Escape HTML special characters."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _inline_md_to_html(text: str) -> str:
    """Convert inline Markdown to HTML (bold, italic, inline code, links)."""
    # Bold
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    # Inline code
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    # Italic
    text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)
    # Links
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)
    return text


# ---------------------------------------------------------------------------
# Export helpers
# ---------------------------------------------------------------------------


def export_docs_to_file(
    docs: GeneratedDocs,
    file_path: str,
    *,
    fmt: str = "markdown",
) -> str:
    """Export generated documentation to a file.

    Args:
        docs: The GeneratedDocs container.
        file_path: Absolute path to the output file.
        fmt: Output format ('markdown' or 'html').

    Returns:
        The absolute path of the written file.

    Raises:
        ValueError: If fmt is unsupported.
        OSError: If the file cannot be written.
    """
    if fmt == "markdown":
        content = format_as_markdown(docs)
    elif fmt == "html":
        content = format_as_html(docs)
    else:
        raise ValueError(
            f"Unsupported format: {fmt!r}. Use 'markdown' or 'html'."
        )

    os.makedirs(os.path.dirname(file_path) or ".", exist_ok=True)
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)

    return os.path.abspath(file_path)


# ---------------------------------------------------------------------------
# Utility: get quick reference
# ---------------------------------------------------------------------------


def get_tool_quick_reference() -> dict[str, str]:
    """Return a quick-reference mapping of tool names to one-line descriptions.

    Useful for inline help or autocomplete suggestions.
    """
    try:
        docs = generate_tool_docs()
    except Exception:
        return {}
    return {d.name: d.description for d in docs}


def get_skill_quick_reference() -> dict[str, str]:
    """Return a quick-reference mapping of skill names to descriptions."""
    try:
        docs = generate_skill_docs()
    except Exception:
        return {}
    return {d.name: d.description for d in docs}


# ---------------------------------------------------------------------------
# Async wrappers (matching the original stub signature)
# ---------------------------------------------------------------------------


async def search_docs_async(query: str, context: str = "") -> MagicDocsResult:
    """Async wrapper for search_docs. Preserves backward compatibility."""
    return search_docs(query, context=context)


async def generate_all_docs_async(
    *,
    tools: list[Any] | None = None,
    skills_dirs: list[str] | None = None,
) -> GeneratedDocs:
    """Async wrapper for generate_all_docs. Preserves backward compatibility."""
    return generate_all_docs(tools=tools, skills_dirs=skills_dirs)
