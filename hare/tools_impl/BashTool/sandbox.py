"""
Sandbox decision logic.

Port of: src/tools/BashTool/shouldUseSandbox.ts
"""

from __future__ import annotations


from hare.tools_impl.BashTool.bash_permissions import (
    bash_permission_rule,
    match_wildcard_pattern,
    strip_all_leading_env_vars,
    strip_safe_wrappers,
    BINARY_HIJACK_VARS,
)
from hare.utils.bash.commands import split_command


def should_use_sandbox(
    command: str | None = None,
    *,
    sandbox_enabled: bool = False,
    dangerously_disable_sandbox: bool = False,
    excluded_commands: list[str] | None = None,
) -> bool:
    """Determine whether a command should run in a sandbox."""
    if not sandbox_enabled:
        return False

    if dangerously_disable_sandbox:
        return False

    if not command:
        return False

    if excluded_commands and _contains_excluded_command(command, excluded_commands):
        return False

    return True


def _get_user_excluded_commands() -> list[str]:
    """User-configured sandbox-excluded commands from settings
    (settings.sandbox.excludedCommands). Best-effort — settings errors yield
    an empty list."""
    try:
        from hare.utils.settings.settings import get_initial_settings

        settings = get_initial_settings()
        sandbox = settings.get("sandbox") if isinstance(settings, dict) else None
        if isinstance(sandbox, dict):
            excluded = sandbox.get("excludedCommands")
            if isinstance(excluded, list):
                return [c for c in excluded if isinstance(c, str)]
    except Exception:
        pass
    return []


def should_use_sandbox_for_input(input: dict) -> bool:
    """Decide whether to sandbox a Bash invocation, consulting the live
    SandboxManager config and user settings.

    Port of TS shouldUseSandbox(input):
      1. off unless SandboxManager.is_sandboxing_enabled()
      2. off if dangerouslyDisableSandbox AND policy allows unsandboxed commands
      3. off if no command
      4. off if the command matches a user-excluded pattern
      5. otherwise on
    """
    from hare.utils.sandbox.sandbox_adapter import SandboxManager

    if not SandboxManager.is_sandboxing_enabled():
        return False

    disable = bool(
        input.get("dangerously_disable_sandbox")
        or input.get("dangerouslyDisableSandbox")
    )
    if disable and SandboxManager.are_unsandboxed_commands_allowed():
        return False

    command = input.get("command")
    if not command:
        return False

    excluded = _get_user_excluded_commands()
    if excluded and _contains_excluded_command(command, excluded):
        return False

    return True


def _contains_excluded_command(command: str, excluded_commands: list[str]) -> bool:
    """Check if any subcommand matches an excluded pattern."""
    try:
        subcommands = split_command(command)
    except Exception:
        subcommands = [command]

    for subcmd in subcommands:
        trimmed = subcmd.strip()
        candidates = _generate_fixed_point_candidates(trimmed)

        for pattern in excluded_commands:
            rule = bash_permission_rule(pattern)
            for cand in candidates:
                if rule.type == "prefix":
                    if cand == rule.prefix or cand.startswith(rule.prefix + " "):
                        return True
                elif rule.type == "exact":
                    if cand == rule.command:
                        return True
                elif rule.type == "wildcard":
                    if match_wildcard_pattern(rule.pattern, cand):
                        return True
    return False


def _generate_fixed_point_candidates(command: str) -> list[str]:
    """Generate all stripped variants of a command."""
    candidates = [command]
    seen = {command}
    start = 0
    while start < len(candidates):
        end = len(candidates)
        for i in range(start, end):
            cmd = candidates[i]
            env_stripped = strip_all_leading_env_vars(cmd, BINARY_HIJACK_VARS)
            if env_stripped not in seen:
                candidates.append(env_stripped)
                seen.add(env_stripped)
            wrapper_stripped = strip_safe_wrappers(cmd)
            if wrapper_stripped not in seen:
                candidates.append(wrapper_stripped)
                seen.add(wrapper_stripped)
        start = end
    return candidates
