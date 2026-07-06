"""Validate plugin.json / marketplace manifests. Port of: src/utils/plugins/validatePlugin.ts"""

from __future__ import annotations

import json as json_module
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from hare.utils.errors import error_message, get_errno_code, is_enoent
from hare.utils.frontmatter_parser import FRONTMATTER_REGEX
from hare.utils.slow_operations import json_parse
from hare.utils.yaml import parse_yaml

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ValidationError:
    path: str
    message: str
    code: str | None = None


@dataclass
class ValidationWarning:
    path: str
    message: str


@dataclass
class ValidationResult:
    success: bool
    errors: list[ValidationError] = field(default_factory=list)
    warnings: list[ValidationWarning] = field(default_factory=list)
    file_path: str = ""
    file_type: Literal[
        "plugin", "marketplace", "skill", "agent", "command", "hooks"
    ] = "plugin"


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MARKETPLACE_ONLY_MANIFEST_FIELDS: frozenset[str] = frozenset(
    {"category", "source", "tags", "strict", "id"}
)

KEBAB_CASE_PATTERN = r"^[a-z0-9]+(-[a-z0-9]+)*$"

REQUIRED_PLUGIN_FIELDS: frozenset[str] = frozenset({"name"})

REQUIRED_MARKETPLACE_FIELDS: frozenset[str] = frozenset({"name", "plugins"})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_file(absolute_path: str) -> str:
    """Read a file as UTF-8 text, raising OSError on failure."""
    return Path(absolute_path).read_text(encoding="utf-8")


def _parse_json(content: str) -> Any:
    """Parse JSON string using the slow-ops wrapper for consistent logging."""
    return json_parse(content)


def _is_kebab_case(name: str) -> bool:
    import re

    return bool(re.match(KEBAB_CASE_PATTERN, name))


# ---------------------------------------------------------------------------
# Manifest type detection
# ---------------------------------------------------------------------------


def detect_manifest_type(
    file_path: str,
) -> Literal["plugin", "marketplace", "unknown"]:
    """Detect whether a file is a plugin manifest or marketplace manifest."""
    file_name = Path(file_path).name
    dir_name = Path(file_path).parent.name

    if file_name == "plugin.json":
        return "plugin"
    if file_name == "marketplace.json":
        return "marketplace"

    if dir_name == ".claude-plugin":
        return "plugin"

    return "unknown"


# ---------------------------------------------------------------------------
# Path traversal check
# ---------------------------------------------------------------------------


def check_path_traversal(
    path_value: str,
    field: str,
    errors: list[ValidationError],
    hint: str | None = None,
) -> None:
    """Check for parent-directory segments ('..') in a path string."""
    if ".." in path_value:
        msg = (
            f'Path contains "..": {path_value}. {hint}'
            if hint
            else f'Path contains ".." which could be a path traversal attempt: {path_value}'
        )
        errors.append(ValidationError(path=field, message=msg))


def marketplace_source_hint(path_value: str) -> str:
    """Compute a tailored suggestion for marketplace source paths with '..'."""
    import re

    stripped = re.sub(r"^(\.\./)+", "", path_value)
    corrected = f"./{stripped}" if stripped != path_value else "./plugins/my-plugin"
    return (
        "Plugin source paths are resolved relative to the marketplace root (the directory "
        "containing .claude-plugin/), not relative to marketplace.json. "
        f'Use "{corrected}" instead of "{path_value}".'
    )


# ---------------------------------------------------------------------------
# Plugin manifest validation (plugin.json)
# ---------------------------------------------------------------------------


def _validate_plugin_schema(
    parsed: Any, absolute_path: str
) -> tuple[list[ValidationError], list[ValidationWarning], Any]:
    """Lightweight schema validation for plugin.json — no Zod dependency.

    Returns (errors, warnings, cleaned_data_or_None)."""
    errors: list[ValidationError] = []
    warnings: list[ValidationWarning] = []

    if not isinstance(parsed, dict):
        errors.append(
            ValidationError(
                path="root",
                message="plugin.json must be a JSON object (mapping/dictionary).",
            )
        )
        return errors, warnings, None

    # --- Path traversal checks BEFORE schema validation -----------------------
    obj: dict[str, Any] = parsed

    for key in ("commands", "agents", "skills"):
        if key in obj:
            items = obj[key]
            if not isinstance(items, list):
                items = [items]
            for i, item in enumerate(items):
                if isinstance(item, str):
                    check_path_traversal(item, f"{key}[{i}]", errors)

    # --- Strip marketplace-only fields (warn, not error) ---------------------
    to_validate = dict(obj)
    stray_keys = [k for k in obj if k in MARKETPLACE_ONLY_MANIFEST_FIELDS]
    for key in stray_keys:
        del to_validate[key]
        warnings.append(
            ValidationWarning(
                path=key,
                message=(
                    f"Field '{key}' belongs in the marketplace entry (marketplace.json), "
                    f"not plugin.json. It's harmless here but unused — Hare ignores it at load time."
                ),
            )
        )

    # --- Required fields ----------------------------------------------------
    if not to_validate.get("name"):
        errors.append(
            ValidationError(
                path="name",
                message="Plugin name cannot be empty.",
            )
        )

    name = to_validate.get("name")
    if isinstance(name, str):
        if " " in name:
            errors.append(
                ValidationError(
                    path="name",
                    message=(
                        'Plugin name cannot contain spaces. Use kebab-case (e.g., "my-plugin").'
                    ),
                )
            )
        if not _is_kebab_case(name):
            warnings.append(
                ValidationWarning(
                    path="name",
                    message=(
                        f'Plugin name "{name}" is not kebab-case. Hare accepts it, but the '
                        f"Claude.ai marketplace sync requires kebab-case (lowercase letters, digits, "
                        f'and hyphens only, e.g., "my-plugin").'
                    ),
                )
            )

    # --- Optional version ----------------------------------------------------
    if not to_validate.get("version"):
        warnings.append(
            ValidationWarning(
                path="version",
                message='No version specified. Consider adding a version following semver (e.g., "1.0.0").',
            )
        )

    # --- Optional description ------------------------------------------------
    if not to_validate.get("description"):
        warnings.append(
            ValidationWarning(
                path="description",
                message="No description provided. Adding a description helps users understand what your plugin does.",
            )
        )

    # --- Optional author -----------------------------------------------------
    if not to_validate.get("author"):
        warnings.append(
            ValidationWarning(
                path="author",
                message="No author information provided. Consider adding author details for plugin attribution.",
            )
        )

    # --- Validate author object if present ----------------------------------
    author = to_validate.get("author")
    if isinstance(author, dict):
        if not author.get("name"):
            errors.append(
                ValidationError(
                    path="author.name",
                    message="Author name cannot be empty.",
                )
            )
        email = author.get("email")
        if email is not None and not isinstance(email, str):
            errors.append(
                ValidationError(
                    path="author.email",
                    message=f"Author email must be a string, got {type(email).__name__}.",
                )
            )
        url = author.get("url")
        if url is not None and not isinstance(url, str):
            errors.append(
                ValidationError(
                    path="author.url",
                    message=f"Author URL must be a string, got {type(url).__name__}.",
                )
            )
    elif author is not None and not isinstance(author, (str, dict)):
        errors.append(
            ValidationError(
                path="author",
                message=f"Author must be a string or an object with name/email/url, got {type(author).__name__}.",
            )
        )

    # --- homepage URL check -------------------------------------------------
    homepage = to_validate.get("homepage")
    if homepage is not None and isinstance(homepage, str):
        if not (homepage.startswith("http://") or homepage.startswith("https://")):
            errors.append(
                ValidationError(
                    path="homepage",
                    message=f'Invalid homepage URL: "{homepage}". Must start with http:// or https://.',
                )
            )

    # --- dependencies check -------------------------------------------------
    dependencies = to_validate.get("dependencies")
    if dependencies is not None:
        if not isinstance(dependencies, list):
            errors.append(
                ValidationError(
                    path="dependencies",
                    message=f"dependencies must be an array, got {type(dependencies).__name__}.",
                )
            )
        else:
            for i, dep in enumerate(dependencies):
                if isinstance(dep, str):
                    if " " in dep:
                        errors.append(
                            ValidationError(
                                path=f"dependencies[{i}]",
                                message=f'Dependency "{dep}" contains spaces. Use kebab-case names.',
                            )
                        )
                elif isinstance(dep, dict):
                    if not dep.get("name"):
                        errors.append(
                            ValidationError(
                                path=f"dependencies[{i}].name",
                                message="Dependency name is required.",
                            )
                        )
                else:
                    errors.append(
                        ValidationError(
                            path=f"dependencies[{i}]",
                            message=f"Dependency must be a string or object, got {type(dep).__name__}.",
                        )
                    )

    # --- Detect unknown top-level keys (strict mode for developer feedback) --
    # We already handled marketplace-only fields; now check for truly unknown ones.
    known_plugin_keys = frozenset(
        {
            "name",
            "version",
            "description",
            "author",
            "homepage",
            "repository",
            "license",
            "keywords",
            "dependencies",
            "commands",
            "agents",
            "skills",
            "hooks",
            "outputStyles",
            "channels",
            "mcpServers",
            "lspServers",
            "settings",
            "userConfig",
            # marketplace-only (already warned and stripped):
            "category",
            "source",
            "tags",
            "strict",
            "id",
        }
    )
    for key in to_validate:
        if key not in known_plugin_keys:
            warnings.append(
                ValidationWarning(
                    path=key,
                    message=f"Unknown field '{key}' in plugin.json. It will be silently ignored at load time.",
                )
            )

    return errors, warnings, to_validate


async def validate_plugin_manifest(file_path: str) -> ValidationResult:
    """Validate a plugin manifest file (plugin.json)."""
    errors: list[ValidationError] = []
    warnings: list[ValidationWarning] = []
    absolute_path = str(Path(file_path).resolve())

    # --- Read file ------------------------------------------------------------
    try:
        content = _read_file(absolute_path)
    except OSError as exc:
        code = get_errno_code(exc)
        if code == "ENOENT":
            message = f"File not found: {absolute_path}"
        elif code == "EISDIR":
            message = f"Path is not a file: {absolute_path}"
        else:
            message = f"Failed to read file: {error_message(exc)}"
        return ValidationResult(
            success=False,
            errors=[ValidationError(path="file", message=message, code=code)],
            file_path=absolute_path,
            file_type="plugin",
        )

    # --- Parse JSON -----------------------------------------------------------
    try:
        parsed = _parse_json(content)
    except Exception as exc:
        return ValidationResult(
            success=False,
            errors=[
                ValidationError(
                    path="json",
                    message=f"Invalid JSON syntax: {error_message(exc)}",
                )
            ],
            file_path=absolute_path,
            file_type="plugin",
        )

    # --- Validate schema -----------------------------------------------------
    schema_errors, schema_warnings, _ = _validate_plugin_schema(parsed, absolute_path)
    errors.extend(schema_errors)
    warnings.extend(schema_warnings)

    return ValidationResult(
        success=len(errors) == 0,
        errors=errors,
        warnings=warnings,
        file_path=absolute_path,
        file_type="plugin",
    )


# ---------------------------------------------------------------------------
# Marketplace manifest validation (marketplace.json)
# ---------------------------------------------------------------------------


def _validate_marketplace_schema(
    parsed: Any, absolute_path: str
) -> tuple[list[ValidationError], list[ValidationWarning], dict[str, Any] | None]:
    """Lightweight schema validation for marketplace.json."""
    errors: list[ValidationError] = []
    warnings: list[ValidationWarning] = []

    if not isinstance(parsed, dict):
        errors.append(
            ValidationError(
                path="root",
                message="marketplace.json must be a JSON object (mapping/dictionary).",
            )
        )
        return errors, warnings, None

    obj: dict[str, Any] = parsed

    # --- Path traversal checks on plugin sources BEFORE schema validation -----
    plugins = obj.get("plugins")
    if isinstance(plugins, list):
        for i, plugin in enumerate(plugins):
            if not isinstance(plugin, dict):
                continue
            source = plugin.get("source")
            if isinstance(source, str):
                check_path_traversal(
                    source,
                    f"plugins[{i}].source",
                    errors,
                    marketplace_source_hint(source),
                )
            if isinstance(source, dict) and isinstance(source.get("path"), str):
                check_path_traversal(
                    source["path"],
                    f"plugins[{i}].source.path",
                    errors,
                )

    # --- Required fields ------------------------------------------------------
    name = obj.get("name")
    if not name or not isinstance(name, str) or not name.strip():
        errors.append(
            ValidationError(path="name", message="Marketplace must have a name.")
        )
    elif isinstance(name, str):
        if " " in name:
            errors.append(
                ValidationError(
                    path="name",
                    message='Marketplace name cannot contain spaces. Use kebab-case (e.g., "my-marketplace").',
                )
            )
        if "/" in name or "\\" in name or ".." in name or name == ".":
            errors.append(
                ValidationError(
                    path="name",
                    message=(
                        'Marketplace name cannot contain path separators (/ or \\), '
                        '".." sequences, or be "."'
                    ),
                )
            )
        if name.lower() == "inline":
            errors.append(
                ValidationError(
                    path="name",
                    message='Marketplace name "inline" is reserved for --plugin-dir session plugins.',
                )
            )
        if name.lower() == "builtin":
            errors.append(
                ValidationError(
                    path="name",
                    message='Marketplace name "builtin" is reserved for built-in plugins.',
                )
            )

    if "plugins" not in obj:
        errors.append(
            ValidationError(path="plugins", message="Marketplace must have a plugins field.")
        )

    # --- Validate each plugin entry ------------------------------------------
    if isinstance(plugins, list):
        seen_names: dict[str, list[int]] = {}
        for i, plugin in enumerate(plugins):
            if not isinstance(plugin, dict):
                errors.append(
                    ValidationError(
                        path=f"plugins[{i}]",
                        message=f"Plugin entry must be an object, got {type(plugin).__name__}.",
                    )
                )
                continue

            pname = plugin.get("name")
            if not pname or not isinstance(pname, str) or not pname.strip():
                errors.append(
                    ValidationError(
                        path=f"plugins[{i}].name",
                        message="Plugin name cannot be empty.",
                    )
                )
            elif isinstance(pname, str):
                if " " in pname:
                    errors.append(
                        ValidationError(
                            path=f"plugins[{i}].name",
                            message=f'Plugin name "{pname}" cannot contain spaces. Use kebab-case.',
                        )
                    )
                seen_names.setdefault(pname, []).append(i)

            psource = plugin.get("source")
            if psource is None:
                errors.append(
                    ValidationError(
                        path=f"plugins[{i}].source",
                        message="Plugin entry must have a source.",
                    )
                )

        # --- Duplicate plugin names -------------------------------------------
        for pname, indices in seen_names.items():
            if len(indices) > 1:
                for idx in indices:
                    errors.append(
                        ValidationError(
                            path=f"plugins[{idx}].name",
                            message=f'Duplicate plugin name "{pname}" found in marketplace.',
                        )
                    )

        # --- Version mismatch check for local sources -------------------------
        manifest_dir = Path(absolute_path).parent
        marketplace_root = (
            manifest_dir.parent
            if manifest_dir.name == ".claude-plugin"
            else manifest_dir
        )
        for i, entry in enumerate(plugins):
            if not isinstance(entry, dict):
                continue
            eversion = entry.get("version")
            esource = entry.get("source")
            if (
                not eversion
                or not isinstance(esource, str)
                or not esource.startswith("./")
            ):
                continue
            plugin_json_path = (
                Path(marketplace_root) / esource / ".claude-plugin" / "plugin.json"
            )
            manifest_version: str | None = None
            try:
                raw = plugin_json_path.read_text(encoding="utf-8")
                mparsed = _parse_json(raw)
                if isinstance(mparsed, dict) and isinstance(mparsed.get("version"), str):
                    manifest_version = mparsed["version"]
            except Exception:
                continue
            if manifest_version and manifest_version != eversion:
                warnings.append(
                    ValidationWarning(
                        path=f"plugins[{i}].version",
                        message=(
                            f'Entry declares version "{eversion}" but '
                            f'{esource}/.claude-plugin/plugin.json says "{manifest_version}". '
                            f"At install time, plugin.json wins (calculatePluginVersion precedence) "
                            f"— the entry version is silently ignored. "
                            f'Update this entry to "{manifest_version}" to match.'
                        ),
                    )
                )

    # --- Owner validation ----------------------------------------------------
    owner = obj.get("owner")
    if isinstance(owner, dict) and not owner.get("name"):
        errors.append(
            ValidationError(
                path="owner.name",
                message="Owner name cannot be empty.",
            )
        )

    # --- Metadata description warning ----------------------------------------
    metadata = obj.get("metadata")
    if isinstance(metadata, dict):
        if not metadata.get("description"):
            warnings.append(
                ValidationWarning(
                    path="metadata.description",
                    message="No marketplace description provided. Adding a description helps users understand what this marketplace offers.",
                )
            )

    # --- Unknown top-level keys warning (strict mode for dev feedback) --------
    known_marketplace_keys = frozenset(
        {
            "name",
            "owner",
            "plugins",
            "forceRemoveDeletedPlugins",
            "metadata",
            "allowCrossMarketplaceDependenciesOn",
        }
    )
    for key in obj:
        if key not in known_marketplace_keys:
            warnings.append(
                ValidationWarning(
                    path=key,
                    message=f"Unknown field '{key}' in marketplace.json. It will be silently ignored at load time.",
                )
            )

    return errors, warnings, obj


async def validate_marketplace_manifest(file_path: str) -> ValidationResult:
    """Validate a marketplace manifest file (marketplace.json)."""
    errors: list[ValidationError] = []
    warnings: list[ValidationWarning] = []
    absolute_path = str(Path(file_path).resolve())

    # --- Read file ------------------------------------------------------------
    try:
        content = _read_file(absolute_path)
    except OSError as exc:
        code = get_errno_code(exc)
        if code == "ENOENT":
            message = f"File not found: {absolute_path}"
        elif code == "EISDIR":
            message = f"Path is not a file: {absolute_path}"
        else:
            message = f"Failed to read file: {error_message(exc)}"
        return ValidationResult(
            success=False,
            errors=[ValidationError(path="file", message=message, code=code)],
            file_path=absolute_path,
            file_type="marketplace",
        )

    # --- Parse JSON -----------------------------------------------------------
    try:
        parsed = _parse_json(content)
    except Exception as exc:
        return ValidationResult(
            success=False,
            errors=[
                ValidationError(
                    path="json",
                    message=f"Invalid JSON syntax: {error_message(exc)}",
                )
            ],
            file_path=absolute_path,
            file_type="marketplace",
        )

    # --- Validate schema -----------------------------------------------------
    schema_errors, schema_warnings, _ = _validate_marketplace_schema(
        parsed, absolute_path
    )
    errors.extend(schema_errors)
    warnings.extend(schema_warnings)

    return ValidationResult(
        success=len(errors) == 0,
        errors=errors,
        warnings=warnings,
        file_path=absolute_path,
        file_type="marketplace",
    )


# ---------------------------------------------------------------------------
# Component file validation (skill / agent / command .md files)
# ---------------------------------------------------------------------------


def validate_component_file(
    file_path: str,
    content: str,
    file_type: Literal["skill", "agent", "command"],
) -> ValidationResult:
    """Validate the YAML frontmatter in a plugin component markdown file."""
    errors: list[ValidationError] = []
    warnings: list[ValidationWarning] = []

    match = FRONTMATTER_REGEX.match(content)
    if not match:
        warnings.append(
            ValidationWarning(
                path="frontmatter",
                message=(
                    "No frontmatter block found. Add YAML frontmatter between --- delimiters "
                    "at the top of the file to set description and other metadata."
                ),
            )
        )
        return ValidationResult(
            success=True,
            errors=errors,
            warnings=warnings,
            file_path=file_path,
            file_type=file_type,
        )

    frontmatter_text = match.group(1) or ""
    try:
        parsed = parse_yaml(frontmatter_text)
    except Exception as exc:
        errors.append(
            ValidationError(
                path="frontmatter",
                message=(
                    f"YAML frontmatter failed to parse: {error_message(exc)}. "
                    f"At runtime this {file_type} loads with empty metadata (all frontmatter "
                    f"fields silently dropped)."
                ),
            )
        )
        return ValidationResult(
            success=False,
            errors=errors,
            warnings=warnings,
            file_path=file_path,
            file_type=file_type,
        )

    if parsed is None or not isinstance(parsed, dict):
        desc = (
            "an array"
            if isinstance(parsed, list)
            else ("null" if parsed is None else type(parsed).__name__)
        )
        errors.append(
            ValidationError(
                path="frontmatter",
                message=f"Frontmatter must be a YAML mapping (key: value pairs), got {desc}.",
            )
        )
        return ValidationResult(
            success=False,
            errors=errors,
            warnings=warnings,
            file_path=file_path,
            file_type=file_type,
        )

    fm: dict[str, Any] = parsed

    # --- description: must be scalar ------------------------------------------
    if "description" in fm:
        desc = fm["description"]
        if not isinstance(desc, (str, int, float, bool)) and desc is not None:
            errors.append(
                ValidationError(
                    path="description",
                    message=(
                        f"description must be a string, got "
                        f"{'array' if isinstance(desc, list) else type(desc).__name__}. "
                        f"At runtime this value is dropped."
                    ),
                )
            )
    else:
        warnings.append(
            ValidationWarning(
                path="description",
                message=(
                    f"No description in frontmatter. A description helps users and Hare "
                    f"understand when to use this {file_type}."
                ),
            )
        )

    # --- name: if present, must be a string ----------------------------------
    if fm.get("name") is not None and not isinstance(fm["name"], str):
        errors.append(
            ValidationError(
                path="name",
                message=f"name must be a string, got {type(fm['name']).__name__}.",
            )
        )

    # --- allowed-tools: string or array of strings ---------------------------
    allowed_tools = fm.get("allowed-tools")
    if allowed_tools is not None:
        if not isinstance(allowed_tools, (str, list)):
            errors.append(
                ValidationError(
                    path="allowed-tools",
                    message=f"allowed-tools must be a string or array of strings, got {type(allowed_tools).__name__}.",
                )
            )
        elif isinstance(allowed_tools, list) and any(
            not isinstance(t, str) for t in allowed_tools
        ):
            errors.append(
                ValidationError(
                    path="allowed-tools",
                    message="allowed-tools array must contain only strings.",
                )
            )

    # --- shell: 'bash' or 'powershell' ---------------------------------------
    shell = fm.get("shell")
    if shell is not None:
        if not isinstance(shell, str):
            errors.append(
                ValidationError(
                    path="shell",
                    message=f"shell must be a string, got {type(shell).__name__}.",
                )
            )
        else:
            normalized = shell.strip().lower()
            if normalized not in ("bash", "powershell"):
                errors.append(
                    ValidationError(
                        path="shell",
                        message=f"shell must be 'bash' or 'powershell', got '{shell}'.",
                    )
                )

    return ValidationResult(
        success=len(errors) == 0,
        errors=errors,
        warnings=warnings,
        file_path=file_path,
        file_type=file_type,
    )


# ---------------------------------------------------------------------------
# hooks.json validation
# ---------------------------------------------------------------------------


async def validate_hooks_json(file_path: str) -> ValidationResult:
    """Validate a plugin's hooks.json file."""
    try:
        content = _read_file(file_path)
    except OSError as exc:
        code = get_errno_code(exc)
        # ENOENT is fine — hooks are optional
        if code == "ENOENT":
            return ValidationResult(
                success=True,
                errors=[],
                warnings=[],
                file_path=file_path,
                file_type="hooks",
            )
        return ValidationResult(
            success=False,
            errors=[
                ValidationError(
                    path="file",
                    message=f"Failed to read file: {error_message(exc)}",
                )
            ],
            file_path=file_path,
            file_type="hooks",
        )

    try:
        parsed = _parse_json(content)
    except Exception as exc:
        return ValidationResult(
            success=False,
            errors=[
                ValidationError(
                    path="json",
                    message=(
                        f"Invalid JSON syntax: {error_message(exc)}. "
                        f"At runtime this breaks the entire plugin load."
                    ),
                )
            ],
            file_path=file_path,
            file_type="hooks",
        )

    if not isinstance(parsed, dict):
        return ValidationResult(
            success=False,
            errors=[
                ValidationError(
                    path="root",
                    message="hooks.json must be a JSON object (mapping/dictionary).",
                )
            ],
            file_path=file_path,
            file_type="hooks",
        )

    # Basic structure validation — hooks.json should at least have a hooks key
    if "hooks" not in parsed and "description" not in parsed:
        return ValidationResult(
            success=False,
            errors=[
                ValidationError(
                    path="root",
                    message="hooks.json must contain at least 'hooks' or 'description'.",
                )
            ],
            file_path=file_path,
            file_type="hooks",
        )

    return ValidationResult(
        success=True,
        errors=[],
        warnings=[],
        file_path=file_path,
        file_type="hooks",
    )


# ---------------------------------------------------------------------------
# Directory collection
# ---------------------------------------------------------------------------


async def collect_markdown(
    directory: str,
    is_skills_dir: bool,
) -> list[str]:
    """Recursively collect .md files under a directory.

    For skills directories, only collects <name>/SKILL.md (one level deep).
    For commands/agents, recurses fully collecting all .md files.
    """
    dir_path = Path(directory)
    if not dir_path.is_dir():
        return []

    if is_skills_dir:
        results: list[str] = []
        try:
            for entry in sorted(dir_path.iterdir()):
                if entry.is_dir():
                    skill_md = entry / "SKILL.md"
                    results.append(str(skill_md))
        except OSError:
            return []
        return results

    # Commands/agents: recurse and collect all .md files.
    results: list[str] = []
    try:
        for entry in sorted(dir_path.iterdir()):
            if entry.is_dir():
                results.extend(await collect_markdown(str(entry), False))
            elif entry.is_file() and entry.name.lower().endswith(".md"):
                results.append(str(entry))
    except OSError:
        pass
    return results


# ---------------------------------------------------------------------------
# Plugin contents validation (directory scan)
# ---------------------------------------------------------------------------


async def validate_plugin_contents(plugin_dir: str) -> list[ValidationResult]:
    """Validate all content files inside a plugin directory.

    Scans skills/, agents/, commands/, and hooks/hooks.json.
    Returns one ValidationResult per file that has errors or warnings.
    """
    results: list[ValidationResult] = []

    dirs: list[tuple[Literal["skill", "agent", "command"], str]] = [
        ("skill", str(Path(plugin_dir) / "skills")),
        ("agent", str(Path(plugin_dir) / "agents")),
        ("command", str(Path(plugin_dir) / "commands")),
    ]

    for file_type, directory in dirs:
        files = await collect_markdown(directory, file_type == "skill")
        for file_path_str in files:
            try:
                content = _read_file(file_path_str)
            except OSError as exc:
                # ENOENT is expected for speculative skill paths (subdirs without SKILL.md)
                if is_enoent(exc):
                    continue
                results.append(
                    ValidationResult(
                        success=False,
                        errors=[
                            ValidationError(
                                path="file",
                                message=f"Failed to read: {error_message(exc)}",
                            )
                        ],
                        file_path=file_path_str,
                        file_type=file_type,
                    )
                )
                continue

            r = validate_component_file(file_path_str, content, file_type)
            if r.errors or r.warnings:
                results.append(r)

    hooks_result = await validate_hooks_json(
        str(Path(plugin_dir) / "hooks" / "hooks.json")
    )
    if hooks_result.errors or hooks_result.warnings:
        results.append(hooks_result)

    return results


# ---------------------------------------------------------------------------
# Top-level entry points
# ---------------------------------------------------------------------------


async def validate_manifest(file_path: str) -> ValidationResult:
    """Validate a manifest file or directory (auto-detects type).

    If file_path is a directory, looks for .claude-plugin/marketplace.json
    first, then .claude-plugin/plugin.json inside it.
    """
    absolute_path = str(Path(file_path).resolve())

    # --- Check if path is a directory ----------------------------------------
    p = Path(absolute_path)
    if p.is_dir():
        marketplace_path = p / ".claude-plugin" / "marketplace.json"
        marketplace_result = await validate_marketplace_manifest(str(marketplace_path))
        # Only fall through if marketplace file was not found
        first_error_code = (
            marketplace_result.errors[0].code if marketplace_result.errors else None
        )
        if first_error_code != "ENOENT":
            return marketplace_result

        plugin_path = p / ".claude-plugin" / "plugin.json"
        plugin_result = await validate_plugin_manifest(str(plugin_path))
        first_error_code = plugin_result.errors[0].code if plugin_result.errors else None
        if first_error_code != "ENOENT":
            return plugin_result

        return ValidationResult(
            success=False,
            errors=[
                ValidationError(
                    path="directory",
                    message="No manifest found in directory. Expected .claude-plugin/marketplace.json or .claude-plugin/plugin.json",
                )
            ],
            file_path=absolute_path,
            file_type="plugin",
        )

    # --- Detect type from filename and validate ------------------------------
    manifest_type = detect_manifest_type(file_path)

    if manifest_type == "plugin":
        return await validate_plugin_manifest(file_path)

    if manifest_type == "marketplace":
        return await validate_marketplace_manifest(file_path)

    # --- Unknown type — try heuristic based on content -----------------------
    try:
        content = _read_file(absolute_path)
        parsed = _parse_json(content)
        if isinstance(parsed, dict) and isinstance(parsed.get("plugins"), list):
            return await validate_marketplace_manifest(file_path)
    except OSError as exc:
        if get_errno_code(exc) == "ENOENT":
            return ValidationResult(
                success=False,
                errors=[
                    ValidationError(
                        path="file",
                        message=f"File not found: {absolute_path}",
                    )
                ],
                file_path=absolute_path,
                file_type="plugin",
            )
    except Exception:
        pass

    # Default: validate as plugin manifest
    return await validate_plugin_manifest(file_path)


async def validate_plugin_file(path: str | Path) -> ValidationResult:
    """Public entry point — validate a plugin or marketplace manifest file/directory.

    Delegates to validate_manifest() which auto-detects manifest type
    (plugin.json vs marketplace.json) and handles both files and directories.

    Returns a ValidationResult with success=True if validation passes,
    or success=False with a list of errors and warnings.
    """
    file_path = str(Path(path).resolve())

    # Early: check if the path exists at all (file or dir)
    p = Path(file_path)
    if not p.exists():
        return ValidationResult(
            success=False,
            errors=[
                ValidationError(
                    path="root",
                    message=f"File not found: {file_path}",
                    code="ENOENT",
                )
            ],
            file_path=file_path,
            file_type="plugin",
        )

    # If it's a plain json file, delegate directly
    if p.is_file():
        return await validate_manifest(file_path)

    # If it's a directory, delegate to validate_manifest (which will look for
    # .claude-plugin/marketplace.json and .claude-plugin/plugin.json)
    if p.is_dir():
        return await validate_manifest(file_path)

    return ValidationResult(
        success=False,
        errors=[
            ValidationError(
                path="root",
                message=f"Path is neither a file nor a directory: {file_path}",
            )
        ],
        file_path=file_path,
        file_type="plugin",
    )
