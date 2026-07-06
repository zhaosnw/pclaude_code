"""
SkillTool - invoke a slash-command skill.

Port of: src/tools/SkillTool/SkillTool.ts

Supports inline and forked execution modes, extracts tool restrictions/
model/effort overrides from skill frontmatter, and provides permission
checks via deny/ask/allow rule matching.
"""

from __future__ import annotations

from typing import Any

TOOL_NAME = "Skill"

_FORKED_SKILLS: frozenset[str] = frozenset({
    "verify", "executing-plans", "deep-research", "run",
    "webapp-testing", "subagent-driven-development",
})


def _extract_allowed_tools(command: dict[str, Any]) -> list[str] | None:
    allowed = command.get("allowedTools") or command.get("tools")
    if isinstance(allowed, list) and allowed:
        return [str(t) for t in allowed]
    return None


def _extract_model_override(command: dict[str, Any]) -> str | None:
    for key in ("model", "modelOverride"):
        val = command.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def _extract_effort_override(command: dict[str, Any]) -> str | None:
    _VALID = frozenset({"low", "medium", "high", "max"})
    for key in ("effort", "thinkingEffort", "thinking_effort"):
        val = command.get(key)
        if isinstance(val, str) and val.lower() in _VALID:
            return val.lower()
    return None


def _should_fork(command: dict[str, Any]) -> bool:
    name = str(command.get("name", "") or "").lower()
    if name in _FORKED_SKILLS:
        return True
    if command.get("fork") is True:
        return True
    if str(command.get("executionMode", "") or "").lower() == "forked":
        return True
    return False


async def check_permissions(input: dict[str, Any], context: Any) -> Any:
    from hare.app_types.permissions import (PermissionAllowDecision, PermissionAskDecision, PermissionDenyDecision)
    skill_name = str(input.get("skill", "") or "").lower().lstrip("/")
    if not skill_name:
        return PermissionDenyDecision(behavior="deny", message="Skill name is required.")

    perm_context = getattr(context, "options", context)

    def _matches(rule_name: str) -> bool:
        r = rule_name.lower().lstrip("/")
        return r == skill_name or skill_name.startswith(r + ":")

    deny_rules: dict = getattr(perm_context, "always_deny_rules", {}) or {}
    for rules in deny_rules.values():
        for rule in rules:
            if _matches(rule):
                return PermissionDenyDecision(behavior="deny", message=f"Skill '{skill_name}' is denied.")

    allow_rules: dict = getattr(perm_context, "always_allow_rules", {}) or {}
    for rules in allow_rules.values():
        for rule in rules:
            if _matches(rule):
                return PermissionAllowDecision(behavior="allow", updated_input=input)

    return PermissionAllowDecision(behavior="allow", updated_input=input)


async def _run_skill_forked(command: dict[str, Any], args: str, context: Any) -> dict[str, Any]:
    skill_name = command.get("name", "")
    skill_desc = command.get("description", "") or ""
    when_to_use = command.get("whenToUse", "") or ""
    full_desc = f"{skill_desc} - {when_to_use}" if when_to_use else skill_desc
    return {"skill": skill_name, "mode": "forked", "description": full_desc, "args": args,
            "allowed_tools": _extract_allowed_tools(command),
            "model_override": _extract_model_override(command),
            "effort_override": _extract_effort_override(command),
            "message": f"Skill '{skill_name}' dispatched in forked mode."}


async def _run_skill_inline(command: dict[str, Any], args: str, context: Any) -> dict[str, Any]:
    skill_name = command.get("name", "")
    arg_list = args.split() if args else []
    try:
        result = await command["call"](arg_list, context)
        return {"data": result, "mode": "inline", "skill": skill_name}
    except Exception as e:
        return {"error": f"Skill '{skill_name}' failed: {e}", "mode": "inline", "skill": skill_name}


def input_schema() -> dict[str, Any]:
    return {"type": "object",
            "properties": {"skill": {"type": "string", "description": "Skill name (without /)"},
                          "args": {"type": "string", "description": "Optional arguments"}},
            "required": ["skill"]}


async def call(skill: str, args: str = "", context: Any = None, **kwargs: Any) -> dict[str, Any]:
    from hare.commands_impl import find_command

    clean_name = skill.lower().lstrip("/").strip()
    if not clean_name:
        return {"error": "Skill name is required."}

    cmd = find_command(clean_name)
    if cmd is None or "call" not in cmd:
        # Fuzzy suggestions
        from hare.commands_impl import get_all_command_definitions
        all_cmds = get_all_command_definitions()
        candidates = [c.get("name", "") for c in all_cmds
                      if clean_name in str(c.get("name", "")).lower() or
                      str(c.get("name", "")).lower() in clean_name][:5]
        suggestion = f" Did you mean: {', '.join(f'/{c}' for c in candidates)}?" if candidates else ""
        return {"error": f"Skill '{skill}' not found.{suggestion}"}

    if _should_fork(cmd):
        return await _run_skill_forked(cmd, args, context)
    return await _run_skill_inline(cmd, args, context)
