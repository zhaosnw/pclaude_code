"""
Plugin dependency resolution (pure logic, minimal I/O).

Port of: src/utils/plugins/dependencyResolver.ts
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypedDict

from hare.utils.plugins.plugin_identifier import parse_plugin_identifier
from hare.utils.settings.constants import EditableSettingSource
from hare.utils.settings.settings import get_settings_for_source

PluginId = str
INLINE_MARKETPLACE = "inline"


@dataclass
class DependencyLookupResult:
    dependencies: list[str] | None = None


class PluginError(TypedDict, total=False):
    type: str
    source: str
    plugin: str
    dependency: str
    reason: str


@dataclass
class LoadedPlugin:
    """Minimal shape for verify_and_demote / find_reverse_dependents."""

    source: str
    name: str
    enabled: bool
    manifest: dict[str, Any]


ResolutionResult = (
    dict[str, Any]  # ok: true, closure | error shapes
)


def qualify_dependency(dep: str, declaring_plugin_id: str) -> str:
    parsed_dep = parse_plugin_identifier(dep)
    if parsed_dep.marketplace:
        return dep
    mkt = parse_plugin_identifier(declaring_plugin_id).marketplace
    if not mkt or mkt == INLINE_MARKETPLACE:
        return dep
    return f"{dep}@{mkt}"


async def resolve_dependency_closure(
    root_id: PluginId,
    lookup: Any,
    already_enabled: frozenset[PluginId] | set[PluginId],
    allowed_cross_marketplaces: frozenset[str] | set[str] | None = None,
) -> dict[str, Any]:
    allowed = allowed_cross_marketplaces or frozenset()
    root_marketplace = parse_plugin_identifier(root_id).marketplace
    closure: list[str] = []
    visited: set[str] = set()
    stack: list[str] = []

    async def walk(id_: str, required_by: str) -> dict[str, Any] | None:
        if id_ != root_id and id_ in already_enabled:
            return None
        id_marketplace = parse_plugin_identifier(id_).marketplace
        if id_marketplace != root_marketplace and not (
            id_marketplace and id_marketplace in allowed
        ):
            return {
                "ok": False,
                "reason": "cross-marketplace",
                "dependency": id_,
                "requiredBy": required_by,
            }
        if id_ in stack:
            return {"ok": False, "reason": "cycle", "chain": stack + [id_]}
        if id_ in visited:
            return None
        visited.add(id_)

        entry = await lookup(id_)
        if not entry:
            return {
                "ok": False,
                "reason": "not-found",
                "missing": id_,
                "requiredBy": required_by,
            }

        if isinstance(entry, DependencyLookupResult):
            deps = entry.dependencies
        else:
            deps = entry.get("dependencies")
        stack.append(id_)
        for raw_dep in deps or []:
            dep = qualify_dependency(raw_dep, id_)
            err = await walk(dep, id_)
            if err:
                stack.pop()
                return err
        stack.pop()

        closure.append(id_)
        return None

    err = await walk(root_id, root_id)
    if err:
        return err
    return {"ok": True, "closure": closure}


def verify_and_demote(
    plugins: list[LoadedPlugin],
) -> tuple[set[str], list[PluginError]]:
    known = {p.source for p in plugins}
    enabled = {p.source for p in plugins if p.enabled}
    known_by_name = {parse_plugin_identifier(p.source).name for p in plugins}
    enabled_by_name: dict[str, int] = {}
    for pid in enabled:
        n = parse_plugin_identifier(pid).name
        enabled_by_name[n] = enabled_by_name.get(n, 0) + 1

    errors: list[PluginError] = []
    changed = True
    while changed:
        changed = False
        for p in plugins:
            if p.source not in enabled:
                continue
            for raw_dep in p.manifest.get("dependencies") or []:
                dep = qualify_dependency(raw_dep, p.source)
                is_bare = not parse_plugin_identifier(dep).marketplace
                satisfied = (
                    (enabled_by_name.get(dep, 0) > 0) if is_bare else dep in enabled
                )
                if not satisfied:
                    enabled.discard(p.source)
                    count = enabled_by_name.get(p.name, 0)
                    if count <= 1:
                        enabled_by_name.pop(p.name, None)
                    else:
                        enabled_by_name[p.name] = count - 1
                    present = dep in known_by_name if is_bare else dep in known
                    errors.append(
                        {
                            "type": "dependency-unsatisfied",
                            "source": p.source,
                            "plugin": p.name,
                            "dependency": dep,
                            "reason": "not-enabled" if present else "not-found",
                        }
                    )
                    changed = True
                    break

    demoted = {p.source for p in plugins if p.enabled and p.source not in enabled}
    return demoted, errors


def find_reverse_dependents(
    plugin_id: PluginId, plugins: list[LoadedPlugin]
) -> list[str]:
    target_name = parse_plugin_identifier(plugin_id).name
    out: list[str] = []
    for p in plugins:
        if not p.enabled or p.source == plugin_id:
            continue
        for d in p.manifest.get("dependencies") or []:
            qualified = qualify_dependency(d, p.source)
            pid = parse_plugin_identifier(qualified)
            match = (
                qualified == plugin_id if pid.marketplace else qualified == target_name
            )
            if match:
                out.append(p.name)
                break
    return out


def get_enabled_plugin_ids_for_scope(
    setting_source: EditableSettingSource,
) -> set[PluginId]:
    settings = get_settings_for_source(setting_source) or {}
    ep = settings.get("enabledPlugins") or {}
    return {k for k, v in ep.items() if v is True or isinstance(v, list)}


def format_dependency_count_suffix(installed_deps: list[str]) -> str:
    if not installed_deps:
        return ""
    n = len(installed_deps)
    word = "dependency" if n == 1 else "dependencies"
    return f" (+ {n} {word})"


def format_reverse_dependents_suffix(rdeps: list[str] | None) -> str:
    if not rdeps:
        return ""
    return f" — warning: required by {', '.join(rdeps)}"
