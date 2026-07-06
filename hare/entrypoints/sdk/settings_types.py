"""
SDK settings types.

Port of: src/entrypoints/sdk/settingsTypes.generated.ts
"""

from __future__ import annotations

from typing import Any, Literal


PermissionMode = Literal["default", "acceptEdits", "bypassPermissions", "plan"]


class SDKSettings:
    def __init__(
        self,
        model: str | None = None,
        permission_mode: PermissionMode = "default",
        max_turns: int | None = None,
        system_prompt: str | None = None,
        append_system_prompt: str | None = None,
        allowed_tools: list[str] | None = None,
        disallowed_tools: list[str] | None = None,
        mcp_servers: dict[str, Any] | None = None,
    ) -> None:
        self.model = model
        self.permission_mode = permission_mode
        self.max_turns = max_turns
        self.system_prompt = system_prompt
        self.append_system_prompt = append_system_prompt
        self.allowed_tools = allowed_tools
        self.disallowed_tools = disallowed_tools
        self.mcp_servers = mcp_servers
