"""
RemoteTriggerTool – manage scheduled remote Hare agents via API.

Port of: src/tools/RemoteTriggerTool/RemoteTriggerTool.ts
"""

from __future__ import annotations

import json
import urllib.request
import urllib.error
from typing import Any, Optional

from hare.tool import ToolBase, ToolResult, ToolUseContext

REMOTE_TRIGGER_TOOL_NAME = "RemoteTrigger"

DESCRIPTION = (
    "Manage scheduled remote Hare agents (triggers) via the hare.ai CCR API. "
    "Auth is handled in-process — the token never reaches the shell."
)

PROMPT = """Call the hare.ai remote-trigger API. Use this instead of curl — the OAuth token is added automatically in-process and never exposed.

Actions:
- list: GET /v1/code/triggers
- get: GET /v1/code/triggers/{trigger_id}
- create: POST /v1/code/triggers (requires body)
- update: POST /v1/code/triggers/{trigger_id} (requires body, partial update)
- run: POST /v1/code/triggers/{trigger_id}/run

The response is the raw JSON from the API."""

TRIGGERS_BETA = "ccr-triggers-2026-01-30"


class _RemoteTriggerTool(ToolBase):
    name = REMOTE_TRIGGER_TOOL_NAME
    aliases: list[str] = []
    search_hint = "manage scheduled remote agent triggers"
    max_result_size_chars = 100_000

    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "get", "create", "update", "run"],
                },
                "trigger_id": {
                    "type": "string",
                    "description": "Required for get, update, and run",
                },
                "body": {
                    "type": "object",
                    "description": "JSON body for create and update",
                },
            },
            "required": ["action"],
        }

    def is_read_only(self, input: dict[str, Any]) -> bool:
        action = input.get("action", "")
        return action in ("list", "get")

    async def prompt(self, options: dict[str, Any]) -> str:
        return PROMPT

    async def description(self, input: dict[str, Any], options: dict[str, Any]) -> str:
        return DESCRIPTION

    def user_facing_name(self, input: Optional[dict[str, Any]] = None) -> str:
        return REMOTE_TRIGGER_TOOL_NAME

    async def call(
        self,
        args: dict[str, Any],
        context: ToolUseContext,
        can_use_tool: Any = None,
        parent_message: Any = None,
        on_progress: Any = None,
    ) -> ToolResult:
        action = args.get("action", "list")
        trigger_id = args.get("trigger_id")
        body = args.get("body")

        base = "https://api.hare.ai/v1/code/triggers"

        if action == "list":
            method, url, data = "GET", base, None
        elif action == "get":
            if not trigger_id:
                return ToolResult(data="Error: get requires trigger_id", is_error=True)
            method, url, data = "GET", f"{base}/{trigger_id}", None
        elif action == "create":
            if not body:
                return ToolResult(data="Error: create requires body", is_error=True)
            method, url, data = "POST", base, body
        elif action == "update":
            if not trigger_id:
                return ToolResult(
                    data="Error: update requires trigger_id", is_error=True
                )
            if not body:
                return ToolResult(data="Error: update requires body", is_error=True)
            method, url, data = "POST", f"{base}/{trigger_id}", body
        elif action == "run":
            if not trigger_id:
                return ToolResult(data="Error: run requires trigger_id", is_error=True)
            method, url, data = "POST", f"{base}/{trigger_id}/run", {}
        else:
            return ToolResult(data=f"Unknown action: {action}", is_error=True)

        # Stub: actual OAuth token injection would go here
        try:
            req = urllib.request.Request(url, method=method)
            req.add_header("Content-Type", "application/json")
            req.add_header("anthropic-version", "2023-06-01")
            req.add_header("anthropic-beta", TRIGGERS_BETA)
            if data is not None:
                req.data = json.dumps(data).encode()
            with urllib.request.urlopen(req, timeout=20) as resp:
                resp_data = resp.read().decode()
                return ToolResult(data=f"HTTP {resp.status}\n{resp_data}")
        except urllib.error.HTTPError as e:
            body_text = e.read().decode() if e.fp else ""
            return ToolResult(data=f"HTTP {e.code}\n{body_text}")
        except Exception as exc:
            return ToolResult(data=f"Error: {exc}", is_error=True)


RemoteTriggerTool = _RemoteTriggerTool()
