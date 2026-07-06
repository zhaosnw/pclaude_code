"""
SDK core type definitions.

Port of: src/entrypoints/sdk/coreTypes.ts + agentSdkTypes.ts + coreSchemas.ts
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


# ---------------------------------------------------------------------------
# Model usage
# ---------------------------------------------------------------------------


@dataclass
class ModelUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    web_search_requests: int = 0
    cost_usd: float = 0.0
    context_window: int = 0
    max_output_tokens: int = 0


# ---------------------------------------------------------------------------
# Output format
# ---------------------------------------------------------------------------

OutputFormatType = Literal["json_schema"]


@dataclass
class JsonSchemaOutputFormat:
    type: Literal["json_schema"] = "json_schema"
    schema: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

ApiKeySource = Literal["user", "project", "org", "temporary", "oauth"]
ConfigScope = Literal["local", "user", "project"]
SdkBeta = Literal["context-1m-2025-08-07"]


@dataclass
class CoreConfig:
    model: str = ""
    max_turns: int = 100
    system_prompt: str = ""
    tools: list[str] = field(default_factory=list)
    permission_mode: str = "default"
    output_format: JsonSchemaOutputFormat | None = None
    betas: list[str] | None = None
    thinking_config: dict[str, Any] | None = None
    max_thinking_tokens: int | None = None
    api_key_source: ApiKeySource | None = None
    mcp_servers: dict[str, Any] = field(default_factory=dict)
    allowed_tools: list[str] | None = None
    continue_: bool = False
    resume: str | None = None
    verbose: bool = False


# ---------------------------------------------------------------------------
# Thinking
# ---------------------------------------------------------------------------

ThinkingType = Literal["adaptive", "enabled", "disabled"]


@dataclass
class ThinkingAdaptive:
    type: Literal["adaptive"] = "adaptive"


@dataclass
class ThinkingEnabled:
    type: Literal["enabled"] = "enabled"
    budget_tokens: int | None = None


@dataclass
class ThinkingDisabled:
    type: Literal["disabled"] = "disabled"


ThinkingConfig = ThinkingAdaptive | ThinkingEnabled | ThinkingDisabled


# ---------------------------------------------------------------------------
# MCP server config
# ---------------------------------------------------------------------------


@dataclass
class McpStdioServerConfig:
    type: Literal["stdio"] = "stdio"
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)


@dataclass
class McpSSEServerConfig:
    type: Literal["sse"] = "sse"
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class McpHttpServerConfig:
    type: Literal["http"] = "http"
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class McpSdkServerConfig:
    type: Literal["sdk"] = "sdk"
    name: str = ""


@dataclass
class McpClaudeAIProxyServerConfig:
    type: Literal["claudeai-proxy"] = "claudeai-proxy"
    url: str = ""
    id: str = ""


McpServerConfig = (
    McpStdioServerConfig | McpSSEServerConfig | McpHttpServerConfig | McpSdkServerConfig
)
McpServerStatusConfig = McpServerConfig | McpClaudeAIProxyServerConfig


@dataclass
class McpServerStatus:
    name: str = ""
    status: Literal["connected", "failed", "needs-auth", "pending", "disabled"] = (
        "pending"
    )
    server_info: dict[str, str] | None = None
    error: str | None = None
    config: dict[str, Any] | None = None
    scope: str | None = None
    tools: list[dict[str, Any]] | None = None
    capabilities: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Permission types
# ---------------------------------------------------------------------------

PermissionBehavior = Literal["allow", "deny", "ask"]
PermissionUpdateDestination = Literal[
    "userSettings", "projectSettings", "localSettings", "session", "cliArg"
]


@dataclass
class PermissionRuleValue:
    tool_name: str = ""
    rule_content: str | None = None


@dataclass
class PermissionUpdate:
    type: Literal[
        "addRules",
        "replaceRules",
        "removeRules",
        "addDirectories",
        "removeDirectories",
        "setMode",
    ] = "addRules"
    rules: list[PermissionRuleValue] = field(default_factory=list)
    behavior: PermissionBehavior = "allow"
    destination: PermissionUpdateDestination = "session"
    directories: list[str] = field(default_factory=list)
    mode: str | None = None


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass
class CoreResult:
    messages: list[dict[str, Any]] = field(default_factory=list)
    stop_reason: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    result: str = ""
    is_error: bool = False
    total_cost_usd: float = 0.0
    duration_ms: int = 0
    num_turns: int = 0
    session_id: str = ""
    permission_denials: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# SDK message types
# ---------------------------------------------------------------------------


@dataclass
class SDKUserMessage:
    type: Literal["user"] = "user"
    message: dict[str, Any] = field(default_factory=dict)
    session_id: str = ""
    uuid: str = ""
    parent_tool_use_id: str | None = None


@dataclass
class SDKAssistantMessage:
    type: Literal["assistant"] = "assistant"
    message: dict[str, Any] = field(default_factory=dict)
    session_id: str = ""
    uuid: str = ""
    parent_uuid: str | None = None


SDKMessage = dict[str, Any]  # SDKUserMessage | SDKAssistantMessage | ...


# ---------------------------------------------------------------------------
# Hook events
# ---------------------------------------------------------------------------

HOOK_EVENTS = [
    "pre_tool_use",
    "post_tool_use",
    "pre_compact",
    "post_compact",
    "user_prompt_submit",
    "session_start",
    "subagent_start",
    "stop",
    "notification",
    "pre_message",
    "post_message",
]

HookEvent = str  # one of HOOK_EVENTS
