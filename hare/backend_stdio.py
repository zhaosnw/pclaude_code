"""
Stdio backend entrypoint for the TS/Ink frontend.

This provides a minimal NDJSON protocol so the interactive frontend can use
the Python QueryEngine as the system of record without going through the
temporary HTTP bridge.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import sys
from typing import Any, Optional

VERSION = "2.1.88"  # inline to avoid namespace-package import issue
from hare.bootstrap.state import (
    get_session_id,
    set_is_non_interactive_session,
    set_original_cwd,
    set_project_root,
)
from hare.commands import find_command, get_commands
from hare.query_engine import QueryEngine
from hare.sdk import HareClient, HareClientOptions
from hare.session_setup import setup
from hare.utils.cwd import get_cwd, set_cwd


def _emit(event: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(event, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _to_jsonable(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return {k: _to_jsonable(v) for k, v in dataclasses.asdict(value).items()}
    if isinstance(value, dict):
        return {str(k): _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(v) for v in value]
    return value


def _error_event(message: str, *, request_id: Optional[str] = None) -> dict[str, Any]:
    event: dict[str, Any] = {"type": "error", "error": message}
    if request_id is not None:
        event["request_id"] = request_id
    return event


class BackendSession:
    def __init__(
        self,
        *,
        cwd: str,
        model: Optional[str] = None,
        max_turns: Optional[int] = None,
        verbose: bool = False,
        system_prompt: Optional[str] = None,
        append_system_prompt: Optional[str] = None,
    ) -> None:
        self._cwd = cwd
        self._model = model
        self._max_turns = max_turns
        self._verbose = verbose
        self._system_prompt = system_prompt
        self._append_system_prompt = append_system_prompt
        self._commands: list[Any] = []
        self._engine: Optional[QueryEngine] = None
        self._client: Optional[HareClient] = None
        self._active_task: Optional[asyncio.Task[None]] = None

    async def initialize(self) -> None:
        set_cwd(self._cwd)
        set_original_cwd(self._cwd)
        set_project_root(self._cwd)
        set_is_non_interactive_session(True)

        # Strip shell-inherited auth tokens so ~/.hare/settings.json is the
        # authoritative credential source. An empty string is as bad as a stale
        # token (causes "Illegal header value b'Bearer '" from the SDK).
        for _var in ("ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"):
            os.environ.pop(_var, None)

        try:
            from hare.utils.config_full import enable_configs

            enable_configs()
        except ImportError:
            pass

        from hare.utils.ca_certs_config import apply_extra_ca_certs_from_config
        from hare.utils.managed_env import (
            apply_config_environment_variables,
            apply_safe_config_environment_variables,
        )

        apply_safe_config_environment_variables(project_dir=self._cwd)
        apply_extra_ca_certs_from_config()
        apply_config_environment_variables(project_dir=self._cwd)

        await setup(cwd=self._cwd, permission_mode="default")

        self._commands = await get_commands(get_cwd())
        self._client = await HareClient.create(
            HareClientOptions(
                cwd=get_cwd(),
                model=self._model,
                max_turns=self._max_turns,
                verbose=self._verbose,
                system_prompt=self._system_prompt,
                append_system_prompt=self._append_system_prompt,
            )
        )
        self._engine = self._client.engine

    def init_payload(self) -> dict[str, Any]:
        return {
            "type": "init",
            "session_id": get_session_id(),
            "cwd": get_cwd(),
            "version": VERSION,
            "model": self._model,
            "commands": [
                {
                    "name": cmd.name,
                    "description": cmd.description,
                    "aliases": list(cmd.aliases or []),
                    "type": cmd.type,
                }
                for cmd in self._commands
            ],
        }

    async def submit_prompt(self, prompt: str, request_id: Optional[str] = None) -> None:
        await self.submit_prompt_with_context(prompt, request_id=request_id)

    async def submit_prompt_with_context(
        self,
        prompt: str,
        *,
        request_id: Optional[str] = None,
        system_prompt: Optional[list[str]] = None,
        user_context: Optional[dict[str, str]] = None,
        system_context: Optional[dict[str, str]] = None,
    ) -> None:
        if self._engine is None:
            _emit(
                _error_event(
                    "Backend session is not initialized.", request_id=request_id
                )
            )
            return
        if self._active_task is not None and not self._active_task.done():
            _emit(
                _error_event(
                    "Another request is already running.", request_id=request_id
                )
            )
            return

        async def _run() -> None:
            try:
                assert self._engine is not None
                async for event in self._engine.submit_message(
                    prompt,
                    system_prompt_override=system_prompt,
                    user_context_override=user_context,
                    system_context_override=system_context,
                    query_source_override="sdk",
                ):
                    payload = _to_jsonable(dict(event))
                    if request_id is not None:
                        payload["request_id"] = request_id
                    _emit(payload)
            except Exception as exc:
                _emit(_error_event(str(exc), request_id=request_id))
            finally:
                if request_id is not None:
                    _emit({"type": "request_complete", "request_id": request_id})
                self._active_task = None

        self._active_task = asyncio.create_task(_run())

    async def handle_command(
        self,
        raw_input: str,
        request_id: Optional[str] = None,
    ) -> None:
        if self._engine is None:
            _emit(
                _error_event(
                    "Backend session is not initialized.", request_id=request_id
                )
            )
            return
        cmd_name = raw_input.split()[0][1:] if raw_input.startswith("/") else raw_input
        cmd = find_command(cmd_name, self._commands)
        if cmd is None:
            _emit(
                {
                    "type": "command_result",
                    "request_id": request_id,
                    "command": cmd_name,
                    "handled": False,
                    "error": f"Unknown command: /{cmd_name}",
                }
            )
            return
        if cmd.type != "local":
            _emit(
                {
                    "type": "command_result",
                    "request_id": request_id,
                    "command": cmd_name,
                    "handled": False,
                    "error": "Only local slash commands are currently supported through stdio backend.",
                }
            )
            return
        try:
            result = await cmd.call(raw_input, {})
            _emit(
                {
                    "type": "command_result",
                    "request_id": request_id,
                    "command": cmd_name,
                    "handled": True,
                    "result": result,
                }
            )
        except Exception as exc:
            _emit(
                {
                    "type": "command_result",
                    "request_id": request_id,
                    "command": cmd_name,
                    "handled": True,
                    "error": str(exc),
                }
            )

    def interrupt(self, request_id: Optional[str] = None) -> None:
        if self._engine is None:
            _emit(
                _error_event(
                    "Backend session is not initialized.", request_id=request_id
                )
            )
            return
        self._engine.interrupt()
        _emit({"type": "interrupt_ack", "request_id": request_id, "interrupted": True})

    async def wait_for_active_request(self) -> None:
        if self._active_task is not None:
            await self._active_task


async def run_stdio_backend() -> int:
    # Bun/Node writes UTF-8 to our stdin; default locale encoding can break
    # json.loads on non-ASCII prompts (e.g. Chinese) on some macOS setups.
    for _stream in (sys.stdin, sys.stdout):
        try:
            _stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError, ValueError, TypeError):
            pass

    cwd = os.getcwd()
    session = BackendSession(cwd=cwd)
    await session.initialize()
    _emit(session.init_payload())

    while True:
        line = await asyncio.to_thread(sys.stdin.readline)
        if not line:
            await session.wait_for_active_request()
            break
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            _emit(_error_event("Invalid JSON received on stdin."))
            continue

        msg_type = message.get("type")
        request_id = message.get("request_id")

        if msg_type == "submit_prompt":
            prompt = str(message.get("prompt", ""))
            await session.submit_prompt_with_context(
                prompt,
                request_id=request_id,
                system_prompt=message.get("system_prompt")
                if isinstance(message.get("system_prompt"), list)
                else None,
                user_context=message.get("user_context")
                if isinstance(message.get("user_context"), dict)
                else None,
                system_context=message.get("system_context")
                if isinstance(message.get("system_context"), dict)
                else None,
            )
        elif msg_type == "command":
            raw_input = str(message.get("input", ""))
            await session.handle_command(raw_input, request_id=request_id)
        elif msg_type == "interrupt":
            session.interrupt(request_id=request_id)
        elif msg_type == "ping":
            _emit({"type": "pong", "request_id": request_id})
        elif msg_type == "shutdown":
            await session.wait_for_active_request()
            _emit({"type": "shutdown_ack", "request_id": request_id})
            break
        else:
            _emit(
                _error_event(
                    f"Unsupported message type: {msg_type}", request_id=request_id
                )
            )

    return 0
