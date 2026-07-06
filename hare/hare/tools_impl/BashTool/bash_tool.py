"""
BashTool – execute shell commands.

Port of: src/tools/BashTool/BashTool.tsx

Executes bash/shell commands in the user's working directory.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Optional

from hare.tool import ToolBase, ToolResult, ToolUseContext
from hare.app_types.permissions import (
    PermissionAskDecision,
    PermissionDenyDecision,
    PermissionPassthrough,
    PermissionResult,
    ToolPermissionContext,
)

BASH_TOOL_NAME = "Bash"


class _BashTool(ToolBase):
    name = BASH_TOOL_NAME
    aliases = ["bash", "shell"]
    search_hint = "execute terminal shell commands"
    max_result_size_chars = 100_000

    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The bash command to execute. Can be a simple command or a complex shell expression.",
                },
                "description": {
                    "type": "string",
                    "description": "A short human-readable description of what the command does (5-10 words).",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Optional timeout in milliseconds. Default: 120000 (2 minutes).",
                },
                "run_in_background": {
                    "type": "boolean",
                    "description": "Set to true to run this command in the background. Use TaskOutput to read the output later.",
                },
                "dangerously_disable_sandbox": {
                    "type": "boolean",
                    "description": "When sandboxing is enabled, set true to run this command OUTSIDE the sandbox. Only honored if policy permits unsandboxed commands.",
                },
            },
            "required": ["command"],
        }

    def is_read_only(self, input: dict[str, Any]) -> bool:
        return False

    def is_concurrency_safe(self, input: dict[str, Any]) -> bool:
        return False

    async def check_permissions(
        self, input: dict[str, Any], context: ToolUseContext
    ) -> PermissionResult:
        """Check BashTool permissions against rules from permission_context.

        TS: bashToolHasPermission (bashPermissions.ts) — uses tool_use_context's
        permission_context to match deny/ask/allow rules against the command string.

        Returns passthrough (not allow) to let the main pipeline apply mode-based
        and rule-based checks. This tool defers to the four-stage pipeline.
        """
        command = input.get("command", "")
        if not isinstance(command, str) or not command.strip():
            return PermissionDenyDecision(
                behavior="deny",
                message="Bash command is empty.",
            )

        # Extract permission context from tool_use_context
        # If context itself is a ToolPermissionContext, use it directly
        if isinstance(context, ToolPermissionContext):
            permission_context = context
        else:
            permission_context = getattr(
                getattr(context, "options", None),
                "permission_context",
                None,
            ) or ToolPermissionContext(mode="default")

        # Check deny rules against the command
        from hare.utils.permissions.permissions import get_deny_rules
        from hare.utils.permissions.shell_rule_matching import (
            match_wildcard_pattern,
            permission_rule_extract_prefix,
        )
        from hare.utils.permissions.permission_rule import parse_permission_rule

        deny_rules = get_deny_rules(permission_context)
        for rule_str in deny_rules:
            parsed = parse_permission_rule(rule_str)
            if parsed.tool_name != BASH_TOOL_NAME:
                continue
            if not parsed.rule_content:
                # Tool-level deny: "Bash" denies all bash commands
                return PermissionDenyDecision(
                    behavior="deny",
                    message=f"Bash commands are denied by rule: {rule_str}.",
                )
            rc = parsed.rule_content
            # Prefix matching (colon :* syntax)
            prefix = permission_rule_extract_prefix(rc)
            if prefix is not None:
                if command == prefix or command.startswith(prefix + " "):
                    return PermissionDenyDecision(
                        behavior="deny",
                        message=f"Bash command '{command}' denied by rule: {rule_str}.",
                    )
            # Wildcard matching
            if match_wildcard_pattern(rc, command):
                return PermissionDenyDecision(
                    behavior="deny",
                    message=f"Bash command '{command}' denied by rule: {rule_str}.",
                )

        # Check ask rules
        from hare.utils.permissions.permissions import get_ask_rules

        ask_rules = get_ask_rules(permission_context)
        for rule_str in ask_rules:
            parsed = parse_permission_rule(rule_str)
            if parsed.tool_name != BASH_TOOL_NAME:
                continue
            if not parsed.rule_content:
                return PermissionAskDecision(
                    behavior="ask",
                    message=f"Bash commands require confirmation by rule: {rule_str}.",
                )
            rc = parsed.rule_content
            prefix = permission_rule_extract_prefix(rc)
            if prefix is not None:
                if command == prefix or command.startswith(prefix + " "):
                    return PermissionAskDecision(
                        behavior="ask",
                        message=f"Bash command '{command}' requires confirmation.",
                    )
            if match_wildcard_pattern(rc, command):
                return PermissionAskDecision(
                    behavior="ask",
                    message=f"Bash command '{command}' requires confirmation.",
                )

        # No specific rules matched → passthrough to pipeline
        return PermissionPassthrough(
            behavior="passthrough",
            message=f"Bash '{command[:80]}' — no specific rules matched.",
        )

    async def prompt(self, options: dict[str, Any]) -> str:
        return (
            "Execute a bash command on the user's system. "
            "Use this for file operations, running scripts, installing packages, etc. "
            "Commands run in the user's current working directory."
        )

    async def description(self, input: dict[str, Any], options: dict[str, Any]) -> str:
        return input.get("description", "Execute a bash command")

    def user_facing_name(self, input: Optional[dict[str, Any]] = None) -> str:
        return BASH_TOOL_NAME

    def to_auto_classifier_input(self, input: dict[str, Any]) -> Any:
        return input.get("command", "")

    async def call(
        self,
        args: dict[str, Any],
        context: ToolUseContext,
        can_use_tool: Any = None,
        parent_message: Any = None,
        on_progress: Any = None,
    ) -> ToolResult:
        """Execute a bash command."""
        command = args.get("command", "")
        timeout_ms = args.get("timeout", 120_000)
        timeout_s = timeout_ms / 1000

        from hare.utils.cwd import get_cwd

        cwd = get_cwd()

        # Choose shell based on platform
        if os.name == "nt":
            shell_cmd = ["powershell", "-Command", command]
        else:
            shell_cmd = ["bash", "-c", command]

        # Sandbox wrapping (no-op unless sandboxing is enabled — default off, so
        # the normal Bash path is unchanged). Decision honors the
        # dangerouslyDisableSandbox escape hatch and user-excluded commands.
        shell_cmd = self._maybe_wrap_sandbox(command, args, cwd, shell_cmd)

        if args.get("run_in_background"):
            return await self._run_background(command, shell_cmd, cwd)

        try:

            proc = await asyncio.create_subprocess_exec(
                *shell_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout_s
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return ToolResult(data=f"Command timed out after {timeout_s}s")

            stdout = stdout_bytes.decode("utf-8", errors="replace")
            stderr = stderr_bytes.decode("utf-8", errors="replace")
            exit_code = proc.returncode

            # Build output matching TS format
            parts: list[str] = []
            if stdout:
                parts.append(stdout)
            if stderr:
                parts.append(f"stderr:\n{stderr}")
            if exit_code != 0:
                parts.append(f"Exit code: {exit_code}")

            output = "\n".join(parts) if parts else "(no output)"

            # Truncate if needed
            if len(output) > self.max_result_size_chars:
                output = output[: self.max_result_size_chars] + "\n... (truncated)"

            return ToolResult(data=output)

        except Exception as e:
            return ToolResult(data=f"Error executing command: {e}")

    def _maybe_wrap_sandbox(
        self, command: str, args: dict[str, Any], cwd: str, shell_cmd: list[str]
    ) -> list[str]:
        """Wrap the exec argv in the OS sandbox when sandboxing is enabled for
        this command, otherwise return it unchanged. Best-effort: any failure in
        the sandbox layer must not break command execution."""
        try:
            from hare.tools_impl.BashTool.sandbox import should_use_sandbox_for_input
            from hare.utils.sandbox.sandbox_adapter import (
                SandboxConfig,
                get_sandbox_config,
                wrap_command_for_sandbox,
            )

            decision_input = {
                "command": command,
                "dangerously_disable_sandbox": args.get(
                    "dangerously_disable_sandbox"
                ),
            }
            if not should_use_sandbox_for_input(decision_input):
                return shell_cmd
            cfg = get_sandbox_config()
            if not cfg.enabled:
                # Decision said sandbox, but the config holder is a stub with no
                # roots — synthesize an enabled config scoped to the workspace
                # plus the temp dir (common scratch space), so ordinary commands
                # don't break under the write restriction.
                import tempfile

                allow = [cwd, tempfile.gettempdir(), "/tmp", "/private/tmp"]
                cfg = SandboxConfig(enabled=True, filesystem_allow_write=allow)
            return wrap_command_for_sandbox(shell_cmd, cwd, cfg)
        except Exception:
            return shell_cmd

    async def _run_background(
        self, command: str, shell_cmd: list[str], cwd: str
    ) -> ToolResult:
        """Spawn the command in the background, register it as a task, and return
        immediately. Output is read later via TaskOutput (and stoppable via
        TaskStop) — both use the shared task registry."""
        import time

        from hare.tools_impl.TaskTools.task_create_tool import (
            TaskState,
            generate_task_id,
            register_task,
        )

        task_id = generate_task_id()
        state = TaskState(
            task_id=task_id,
            description=command[:80],
            prompt=command,
            status="running",
            started_at=time.time(),
        )

        async def _runner() -> None:
            proc = None
            try:
                proc = await asyncio.create_subprocess_exec(
                    *shell_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=cwd,
                )
                out_b, err_b = await proc.communicate()
                out = out_b.decode("utf-8", errors="replace")
                err = err_b.decode("utf-8", errors="replace")
                parts = [p for p in (out, (f"stderr:\n{err}" if err else "")) if p]
                if proc.returncode:
                    parts.append(f"Exit code: {proc.returncode}")
                state.result = "\n".join(parts) if parts else "(no output)"
                state.status = "completed" if not proc.returncode else "failed"
            except asyncio.CancelledError:
                # TaskStop cancelled us — kill the underlying process too.
                if proc is not None and proc.returncode is None:
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass
                state.status = "cancelled"
                state.error = "cancelled"
                raise
            except Exception as exc:  # pragma: no cover - defensive
                state.status = "failed"
                state.error = str(exc)
                state.result = f"Error: {exc}"
            finally:
                state.completed_at = time.time()

        state._process = asyncio.create_task(_runner())
        register_task(state)
        return ToolResult(
            data=(
                f"Command running in the background (task_id: {task_id}). "
                f"Use TaskOutput with task_id={task_id} to read its output, or "
                f"TaskStop to stop it."
            )
        )


BashTool = _BashTool()
