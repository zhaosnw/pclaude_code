"""
Session runner — spawn and manage CLI child processes with NDJSON parsing.

Port of: src/bridge/sessionRunner.ts

Full child process manager with:
- NDJSON stdout parsing for activity extraction
- Permission request detection (control_request/can_use_tool)
- First user message detection for session title derivation
- Token refresh forwarding via stdin
- Ring buffer activity tracking (10 items)
- Stderr ring buffer (10 lines)
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any, Optional

from hare.bridge.types import (
    SessionActivity,
    SessionDoneStatus,
    SessionHandle,
    SessionSpawnOpts,
)

MAX_ACTIVITIES = 10
MAX_STDERR_LINES = 10

TOOL_VERBS: dict[str, str] = {
    "Read": "Reading",
    "Write": "Writing",
    "Edit": "Editing",
    "MultiEdit": "Editing",
    "Bash": "Running",
    "Glob": "Searching",
    "Grep": "Searching",
    "WebFetch": "Fetching",
    "WebSearch": "Searching",
    "Task": "Running task",
    "FileReadTool": "Reading",
    "FileWriteTool": "Writing",
    "FileEditTool": "Editing",
    "GlobTool": "Searching",
    "GrepTool": "Searching",
    "BashTool": "Running",
    "NotebookEditTool": "Editing notebook",
    "LSP": "LSP",
}


def safe_filename_id(id_val: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", id_val)[:64]


def _tool_summary(name: str, input_data: dict[str, Any]) -> str:
    verb = TOOL_VERBS.get(name, name)
    target = (
        input_data.get("file_path")
        or input_data.get("filePath")
        or input_data.get("pattern")
        or str(input_data.get("command", ""))[:60]
        or input_data.get("url")
        or input_data.get("query")
        or ""
    )
    return f"{verb} {target}" if target else verb


def _extract_activities(line: str, session_id: str) -> list[SessionActivity]:
    """Parse an NDJSON line and extract session activities."""
    try:
        parsed = json.loads(line)
    except json.JSONDecodeError:
        return []

    if not isinstance(parsed, dict):
        return []

    activities: list[SessionActivity] = []
    now = (
        asyncio.get_event_loop().time() * 1000
        if asyncio.get_event_loop().is_running()
        else __import__("time").time() * 1000
    )

    msg_type = parsed.get("type", "")

    if msg_type == "assistant":
        message = parsed.get("message", {})
        content = message.get("content", [])
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use":
                    name = block.get("name", "Tool")
                    inputs = block.get("input", {}) or {}
                    summary = _tool_summary(name, inputs)
                    activities.append(
                        SessionActivity(
                            type="tool_start", summary=summary, timestamp=now
                        )
                    )
                elif block.get("type") == "text":
                    text = block.get("text", "")
                    if text:
                        activities.append(
                            SessionActivity(
                                type="text", summary=text[:80], timestamp=now
                            )
                        )

    elif msg_type == "result":
        subtype = parsed.get("subtype", "")
        if subtype == "success":
            activities.append(
                SessionActivity(
                    type="result", summary="Session completed", timestamp=now
                )
            )
        elif subtype:
            errors = parsed.get("errors", [])
            error_msg = errors[0] if errors else f"Error: {subtype}"
            activities.append(
                SessionActivity(type="error", summary=error_msg, timestamp=now)
            )

    return activities


def _extract_user_message_text(msg: dict[str, Any]) -> Optional[str]:
    """Extract plain text from the first human-authored user message."""
    if (
        msg.get("parent_tool_use_id") is not None
        or msg.get("isSynthetic")
        or msg.get("isReplay")
    ):
        return None

    message = msg.get("message", {})
    content = message.get("content", "")
    text: Optional[str] = None
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text", "")
                break

    text = text.strip() if text else None
    return text if text else None


class _SessionHandleImpl:
    """Full SessionHandle implementation matching TS interface."""

    def __init__(
        self,
        session_id: str,
        proc: asyncio.subprocess.Process,
        access_token: str,
        on_debug: Any = None,
        on_activity: Any = None,
        on_permission_request: Any = None,
        on_first_user_message: Any = None,
    ) -> None:
        self.session_id = session_id
        self._proc = proc
        self.access_token = access_token
        self._on_debug = on_debug
        self._on_activity = on_activity
        self._on_permission_request = on_permission_request
        self._on_first_user_message = on_first_user_message

        self.activities: list[SessionActivity] = []
        self.current_activity: Optional[SessionActivity] = None
        self.last_stderr: list[str] = []
        self._sigkill_sent = False
        self._first_user_message_seen = False
        self._done_future: asyncio.Future[SessionDoneStatus] = asyncio.Future()

    @property
    def done(self) -> asyncio.Future[SessionDoneStatus]:
        return self._done_future

    def _debug(self, msg: str) -> None:
        if self._on_debug:
            self._on_debug(msg)

    def kill(self) -> None:
        try:
            self._proc.terminate()
        except ProcessLookupError:
            pass

    def force_kill(self) -> None:
        if not self._sigkill_sent and self._proc.returncode is None:
            self._sigkill_sent = True
            try:
                self._proc.kill()
            except ProcessLookupError:
                pass

    def write_stdin(self, data: str) -> None:
        if self._proc.stdin and not self._proc.stdin.is_closing():
            self._proc.stdin.write((data + "\n").encode())

    def update_access_token(self, token: str) -> None:
        self.access_token = token
        self.write_stdin(
            json.dumps(
                {
                    "type": "update_environment_variables",
                    "variables": {"CLAUDE_CODE_SESSION_ACCESS_TOKEN": token},
                }
            )
        )

    async def _process_stdout(self) -> None:
        """Process NDJSON lines from child stdout."""
        if not self._proc.stdout:
            return
        try:
            while True:
                line_bytes = await self._proc.stdout.readline()
                if not line_bytes:
                    break
                line = line_bytes.decode("utf-8", errors="replace").rstrip("\n")

                if not line.strip():
                    continue

                activities = _extract_activities(line, self.session_id)
                for activity in activities:
                    if len(self.activities) >= MAX_ACTIVITIES:
                        self.activities.pop(0)
                    self.activities.append(activity)
                    self.current_activity = activity
                    if self._on_activity:
                        self._on_activity(self.session_id, activity)

                # Check for control_request and first user message
                try:
                    parsed = json.loads(line)
                    if isinstance(parsed, dict):
                        if parsed.get("type") == "control_request":
                            request = parsed.get("request", {})
                            if (
                                request.get("subtype") == "can_use_tool"
                                and self._on_permission_request
                            ):
                                self._on_permission_request(
                                    self.session_id, parsed, self.access_token
                                )
                        elif (
                            parsed.get("type") == "user"
                            and not self._first_user_message_seen
                            and self._on_first_user_message
                        ):
                            text = _extract_user_message_text(parsed)
                            if text:
                                self._first_user_message_seen = True
                                self._on_first_user_message(text)
                except Exception:
                    pass
        except Exception:
            pass

    async def _process_stderr(self) -> None:
        """Process stderr lines for diagnostics."""
        if not self._proc.stderr:
            return
        try:
            while True:
                line_bytes = await self._proc.stderr.readline()
                if not line_bytes:
                    break
                line = line_bytes.decode("utf-8", errors="replace").rstrip("\n")
                if len(self.last_stderr) >= MAX_STDERR_LINES:
                    self.last_stderr.pop(0)
                self.last_stderr.append(line)
        except Exception:
            pass

    async def _wait_done(self) -> None:
        """Wait for process to exit and resolve done future."""
        try:
            returncode = await self._proc.wait()
            if returncode == 0:
                self._done_future.set_result("completed")
            elif returncode and returncode < 0:
                self._done_future.set_result("interrupted")
            else:
                self._done_future.set_result("failed")
        except Exception:
            if not self._done_future.done():
                self._done_future.set_result("failed")


class SessionSpawnerImpl:
    """Full session spawner matching TS createSessionSpawner."""

    def __init__(
        self,
        exec_path: str = "hare",
        script_args: Optional[list[str]] = None,
        env: Optional[dict[str, str]] = None,
        verbose: bool = False,
        sandbox: bool = False,
        debug_file: Optional[str] = None,
        permission_mode: Optional[str] = None,
        on_debug: Any = None,
        on_activity: Any = None,
        on_permission_request: Any = None,
    ) -> None:
        self._exec_path = exec_path
        self._script_args = script_args or []
        self._env = env or {}
        self._verbose = verbose
        self._sandbox = sandbox
        self._debug_file = debug_file
        self._permission_mode = permission_mode
        self._on_debug = on_debug
        self._on_activity = on_activity
        self._on_permission_request = on_permission_request

    def spawn(self, opts: SessionSpawnOpts, directory: str) -> SessionHandle:
        """Spawn a child process with full NDJSON pipeline."""
        safe_id = safe_filename_id(opts.session_id)

        # Build args
        args = list(self._script_args) + [
            "--print",
            "--sdk-url",
            opts.sdk_url,
            "--session-id",
            opts.session_id,
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
            "--replay-user-messages",
        ]
        if self._verbose:
            args.append("--verbose")
        if self._permission_mode:
            args.extend(["--permission-mode", self._permission_mode])

        # Build env
        child_env = {
            **os.environ,
            **self._env,
            "CLAUDE_CODE_OAUTH_TOKEN": "",
            "CLAUDE_CODE_ENVIRONMENT_KIND": "bridge",
            "CLAUDE_CODE_SESSION_ACCESS_TOKEN": opts.access_token,
            "CLAUDE_CODE_POST_FOR_SESSION_INGRESS_V2": "1",
        }
        if self._sandbox:
            child_env["CLAUDE_CODE_FORCE_SANDBOX"] = "1"
        if opts.use_ccr_v2:
            child_env["CLAUDE_CODE_USE_CCR_V2"] = "1"
            child_env["CLAUDE_CODE_WORKER_EPOCH"] = str(opts.worker_epoch or 0)

        if self._on_debug:
            self._on_debug(
                f"[bridge:session] Spawning sessionId={opts.session_id} sdkUrl={opts.sdk_url}"
            )

        # Spawn the child process
        proc = asyncio.get_event_loop().run_until_complete(
            asyncio.create_subprocess_exec(
                self._exec_path,
                *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=directory,
                env={k: v for k, v in child_env.items() if v},
            )
        )

        handle = _SessionHandleImpl(
            session_id=opts.session_id,
            proc=proc,
            access_token=opts.access_token,
            on_debug=self._on_debug,
            on_activity=self._on_activity,
            on_permission_request=self._on_permission_request,
            on_first_user_message=opts.on_first_user_message,
        )

        # Start background stdout/stderr processing
        asyncio.ensure_future(handle._process_stdout())
        asyncio.ensure_future(handle._process_stderr())
        asyncio.ensure_future(handle._wait_done())

        return handle


def create_session_spawner(
    exec_path: str = "hare",
    script_args: Optional[list[str]] = None,
    env: Optional[dict[str, str]] = None,
    verbose: bool = False,
    sandbox: bool = False,
    debug_file: Optional[str] = None,
    permission_mode: Optional[str] = None,
    on_debug: Any = None,
    on_activity: Any = None,
    on_permission_request: Any = None,
) -> SessionSpawnerImpl:
    return SessionSpawnerImpl(
        exec_path=exec_path,
        script_args=script_args,
        env=env,
        verbose=verbose,
        sandbox=sandbox,
        debug_file=debug_file,
        permission_mode=permission_mode,
        on_debug=on_debug,
        on_activity=on_activity,
        on_permission_request=on_permission_request,
    )
