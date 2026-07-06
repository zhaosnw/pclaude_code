"""
MCP utility functions.

Port of: src/services/mcp/utils.ts
"""

from __future__ import annotations

import re
from typing import Any

from hare.services.mcp.types import McpServerConfig, McpStdioServerConfig


def format_server_name(name: str) -> str:
    """Format an MCP server name for display."""
    return name.replace("_", " ").replace("-", " ").title()


def validate_server_config(config: dict[str, Any]) -> list[str]:
    """Validate an MCP server config and return list of errors."""
    errors = []
    transport = config.get("type", "stdio")

    if transport == "stdio":
        if not config.get("command"):
            errors.append("stdio transport requires a 'command' field")
    elif transport in ("sse", "http", "streamable-http", "ws"):
        url = config.get("url", "")
        if not url:
            errors.append(f"{transport} transport requires a 'url' field")
        elif transport == "ws" and not re.match(r"^wss?://", url):
            errors.append(f"Invalid WebSocket URL: {url}")
        elif transport != "ws" and not re.match(r"^https?://", url):
            errors.append(f"Invalid URL: {url}")
    else:
        errors.append(f"Unknown transport type: {transport}")

    return errors


def get_server_display_info(name: str, config: McpServerConfig) -> dict[str, str]:
    """Get display information for an MCP server."""
    info = {"name": name, "display_name": format_server_name(name)}

    if isinstance(config, McpStdioServerConfig):
        info["type"] = "stdio"
        info["command"] = config.command
        if config.args:
            info["args"] = " ".join(config.args)
    else:
        info["type"] = getattr(config, "type", "unknown")
        info["url"] = getattr(config, "url", "")

    return info


def get_logging_safe_mcp_base_url(config: McpServerConfig) -> str | None:
    """Strip query string and trailing slash from MCP HTTP/SSE/WS URL for logging / registry lookup."""
    url = getattr(config, "url", None)
    if not isinstance(url, str):
        return None
    try:
        from urllib.parse import urlparse, urlunparse

        parsed = urlparse(url)
        stripped = urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
        return stripped.rstrip("/")
    except Exception:
        return None


def sanitize_mcp_output(output: str, max_chars: int = 100_000) -> str:
    """Sanitize and truncate MCP tool output."""
    if len(output) > max_chars:
        truncated = output[:max_chars]
        return f"{truncated}\n\n[Output truncated at {max_chars} characters]"
    return output


# ---------------------------------------------------------------------------
# MCP security policy filtering (TS filterMcpServersByPolicy — 12.3.2)
# ---------------------------------------------------------------------------


def filter_mcp_servers_by_policy(
    servers: dict[str, Any],
    *,
    allowed: list[dict[str, Any]] | None = None,
    denied: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Filter MCP server configs by enterprise security policy.

    TS filterMcpServersByPolicy:
    - SDK servers are exempt from policy checks (in-process, host-guaranteed safety)
    - Denied list has ABSOLUTE priority (deny > allow)
    - If allow list is defined, only allowed servers pass
    - Supports 3 match types: serverName, serverCommand, serverUrl (with wildcards)

    Returns (allowed_servers, blocked_servers).
    """
    allowed_out: dict[str, Any] = {}
    blocked_out: dict[str, Any] = {}

    for name, config in servers.items():
        # SDK servers exempt from policy checks (TS: in-process, host-guaranteed)
        transport = (
            config.get("type", "stdio")
            if isinstance(config, dict)
            else getattr(config, "type", "stdio")
        )
        if transport == "sdk":
            allowed_out[name] = config
            continue

        # Deny list check (absolute priority)
        if denied and _matches_policy_list(name, config, denied):
            blocked_out[name] = config
            continue

        # Allow list check (gate — if allow list is defined, only matches pass)
        if allowed:
            if not _matches_policy_list(name, config, allowed):
                blocked_out[name] = config
                continue

        allowed_out[name] = config

    return allowed_out, blocked_out


def _matches_policy_list(
    name: str,
    config: dict[str, Any],
    policies: list[dict[str, Any]],
) -> bool:
    """Check if a server config matches any entry in a policy list.

    TS: supports serverName, serverCommand, serverUrl matching with wildcards.
    """
    for policy in policies:
        # Match by server name
        if "serverName" in policy:
            pattern = policy["serverName"]
            if _wildcard_match(name, pattern):
                return True

        # Match by command array (stdio servers)
        if "serverCommand" in policy:
            expected_cmd = policy["serverCommand"]
            if isinstance(expected_cmd, list):
                actual_cmd = [config.get("command", "")]
                actual_args = config.get("args", [])
                if isinstance(actual_args, list):
                    actual_cmd.extend(actual_args)
                if actual_cmd == expected_cmd:
                    return True

        # Match by URL pattern (remote servers)
        if "serverUrl" in policy:
            url = config.get("url", "")
            if isinstance(url, str) and _wildcard_match(url, policy["serverUrl"]):
                return True

    return False


def _wildcard_match(text: str, pattern: str) -> bool:
    """Match a string against a pattern with * wildcards. TS URL wildcard matching."""
    import fnmatch

    return fnmatch.fnmatch(text, pattern)


# ---------------------------------------------------------------------------
# MCP server dedup (TS dedupPluginMcpServers / dedupClaudeAIMcpServers — 12.3.3)
# ---------------------------------------------------------------------------


def compute_server_signature(name: str, config: dict[str, Any]) -> str | None:
    """Compute dedup signature for an MCP server config.

    TS signature rules:
    - stdio: "stdio:" + JSON.stringify([command, ...args])
    - remote (sse/http/ws): "url:" + originalUrl (without query string)
    - sdk: None (no dedup — each SDK instance is independent)
    """
    transport = config.get("type", "stdio") if isinstance(config, dict) else "stdio"
    if transport == "sdk":
        return None
    if transport == "stdio":
        import json

        cmd = [config.get("command", "")]
        args = config.get("args", [])
        if isinstance(args, list):
            cmd.extend(args)
        return f"stdio:{json.dumps(cmd)}"
    # Remote servers (sse, http, ws, sse-ide, ws-ide, claudeai-proxy)
    url = config.get("url", "")
    if isinstance(url, str):
        # Strip query string as TS does for CCR proxy unpacking
        from urllib.parse import urlparse, urlunparse

        parsed = urlparse(url)
        clean = urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
        return f"url:{clean}"
    return f"url:{url}"


def dedup_mcp_servers(
    servers: dict[str, Any],
    *,
    priority_sources: list[str] | None = None,
) -> dict[str, Any]:
    """Deduplicate MCP server configs by signature.

    TS dedupPluginMcpServers / dedupClaudeAIMcpServers:
    - Servers with the same signature are duplicates
    - First encountered (by priority) wins; later duplicates are suppressed
    - SDK servers (signature=None) are never deduped

    Args:
        servers: {name: config} dict (ordered by insertion = priority)
        priority_sources: optional list of source names for logging

    Returns deduped {name: config} dict.
    """
    seen_signatures: dict[str, str] = {}  # signature → name
    result: dict[str, Any] = {}

    for name, config in servers.items():
        sig = compute_server_signature(name, config)
        if sig is None:
            # SDK server — never dedup
            result[name] = config
            continue
        if sig in seen_signatures:
            # Duplicate — skip (first encountered wins)
            continue
        seen_signatures[sig] = name
        result[name] = config

    return result


# ---------------------------------------------------------------------------
# IDE tool whitelist (TS 12.3.4 — only executeCode + getDiagnostics from IDE)
# ---------------------------------------------------------------------------

IDE_MCP_TOOL_WHITELIST = frozenset(
    [
        "mcp__ide__executeCode",
        "mcp__ide__getDiagnostics",
    ]
)


def is_ide_tool_allowed(tool_name: str) -> bool:
    """Check if a tool from an IDE-type MCP server is in the whitelist.

    TS: only executeCode and getDiagnostics are allowed from IDE MCP servers.
    """
    return tool_name in IDE_MCP_TOOL_WHITELIST


# ---------------------------------------------------------------------------
# Server disabled check (TS isMcpServerDisabled — 12.3 server approval)
# ---------------------------------------------------------------------------


def is_mcp_server_disabled(
    name: str, project_config: dict[str, Any] | None = None
) -> bool:
    """Check if an MCP server is disabled in project config.

    TS isMcpServerDisabled: checks disabledMcpServers list in project settings.
    Also checks if an enabledMcpServers allow-list exists (if so, only those are enabled).
    """
    if not project_config:
        return False

    # Check explicit disable list
    disabled = project_config.get("disabledMcpServers", [])
    if isinstance(disabled, list) and name in disabled:
        return True

    # Check if enabled-only allow-list exists
    enabled_only = project_config.get("enabledMcpServers", [])
    if isinstance(enabled_only, list) and enabled_only:
        return name not in enabled_only

    return False


def is_mcp_tool(tool_name: str) -> bool:
    """Check if a tool name is from an MCP server (P2 — stub)."""
    return tool_name.startswith("mcp__")


# ---------------------------------------------------------------------------
# Tool schema conversion
# ---------------------------------------------------------------------------
# MCP servers expose tool definitions with inputSchema (JSON Schema).
# These must be converted to Claude API compatible input_schema format,
# validated, and wrapped with server-scoped naming.


# Standard JSON Schema type keywords that are NOT properties
_JSON_SCHEMA_NON_PROPERTY_KEYS: frozenset[str] = frozenset(
    {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "description",
        "title",
        "default",
        "examples",
        "enum",
        "const",
        "items",
        "minItems",
        "maxItems",
        "minLength",
        "maxLength",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "pattern",
        "format",
        "anyOf",
        "allOf",
        "oneOf",
        "not",
        "$ref",
        "$schema",
        "definitions",
        "if",
        "then",
        "else",
        "dependentRequired",
        "dependentSchemas",
        "minProperties",
        "maxProperties",
        "uniqueItems",
        "propertyNames",
        "contains",
        "minContains",
        "maxContains",
        "contentEncoding",
        "contentMediaType",
        "prefixItems",
        "unevaluatedItems",
        "unevaluatedProperties",
    }
)


def normalize_input_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Normalize an MCP tool input schema to Claude API compatible format.

    MCP servers may return input schemas with varying conventions (camelCase
    vs snake_case keys, missing ``type``, extra metadata).  This function
    produces a clean, predictable JSON Schema object suitable for the
    Claude API ``input_schema`` field.

    Steps:
    - Strip camelCase aliases (``inputSchema`` → ``input_schema``).
    - Ensure top-level ``type: "object"`` when properties exist.
    - Wrap bare ``items`` into an array schema if present without ``type``.
    - Strip non-standard keys from nested property schemas.
    - Enforce ``additionalProperties: false`` at the top level for strict mode.
    """
    from copy import deepcopy

    normalized = deepcopy(schema)

    # Canonicalize camelCase → snake_case at top level
    _canonicalize_keys(normalized)

    # Ensure the top-level schema has type: "object" when properties are declared
    # but type is missing (common omission in some MCP server implementations)
    if "type" not in normalized:
        if "properties" in normalized:
            normalized["type"] = "object"
        elif "items" in normalized:
            normalized["type"] = "array"

    # If items is present but type is not "array", fix it
    if "items" in normalized and normalized.get("type") != "array":
        normalized["type"] = "array"

    # Strip non-standard keys from nested property schemas
    if "properties" in normalized and isinstance(normalized["properties"], dict):
        for prop_schema in normalized["properties"].values():
            if isinstance(prop_schema, dict):
                _strip_non_schema_keys(prop_schema)

    # Clean up anyOf / oneOf / allOf nested schemas recursively
    for key in ("anyOf", "oneOf", "allOf"):
        if key in normalized and isinstance(normalized[key], list):
            for sub_schema in normalized[key]:
                if isinstance(sub_schema, dict):
                    _strip_non_schema_keys(sub_schema)

    return normalized


def _canonicalize_keys(schema: dict[str, Any]) -> None:
    """In-place canonicalization of known camelCase → snake_case keys in a schema dict."""
    _KEY_MAP: dict[str, str] = {
        "inputSchema": "input_schema",
        "additionalProperties": "additionalProperties",  # already matches
        "minItems": "minItems",
        "maxItems": "maxItems",
        "minLength": "minLength",
        "maxLength": "maxLength",
        "minProperties": "minProperties",
        "maxProperties": "maxProperties",
        "uniqueItems": "uniqueItems",
    }
    for camel, snake in list(_KEY_MAP.items()):
        if camel in schema and camel != snake:
            if snake not in schema:
                schema[snake] = schema.pop(camel)


def _strip_non_schema_keys(schema: dict[str, Any]) -> None:
    """Remove keys from a property-level schema that are not valid JSON Schema keywords."""
    for key in list(schema.keys()):
        if key not in _JSON_SCHEMA_NON_PROPERTY_KEYS:
            del schema[key]


def wrap_mcp_tool(
    server_name: str,
    raw_tool: dict[str, Any],
    *,
    max_description_length: int = 2048,
) -> McpToolInfo:
    """Wrap a raw MCP tool definition dict into a structured ``McpToolInfo``.

    This mirrors the TS ``fetchToolsForClient`` _wrap_tool helper.  The tool
    name is scoped to ``mcp__<server>__<tool>`` and the description is
    truncated to avoid oversized prompts.

    Args:
        server_name: The MCP server name (e.g. ``"github"``).
        raw_tool: Raw tool dict from the MCP ``tools/list`` response.
        max_description_length: Maximum description length (default 2048).

    Returns:
        An ``McpToolInfo`` dataclass instance ready for the tool pool.
    """
    from hare.services.mcp.types import McpToolInfo

    annotations = raw_tool.get("annotations", {})
    # Accept both camelCase (MCP spec) and snake_case (pre-normalized)
    input_schema = raw_tool.get("inputSchema", raw_tool.get("input_schema", {}))
    if isinstance(input_schema, dict):
        input_schema = normalize_input_schema(input_schema)

    tool_name = raw_tool.get("name", "")

    return McpToolInfo(
        name=f"mcp__{server_name}__{tool_name}",
        description=str(raw_tool.get("description", ""))[:max_description_length],
        input_schema=input_schema,
        server_name=server_name,
        annotations={
            "readOnlyHint": bool(annotations.get("readOnlyHint", False)),
            "destructiveHint": bool(annotations.get("destructiveHint", False)),
            "openWorldHint": bool(annotations.get("openWorldHint", False)),
            "idempotentHint": bool(annotations.get("idempotentHint", False)),
        },
        is_mcp=True,
    )


def serialize_tool_for_claude(
    server_name: str,
    tool_name: str,
    raw_tool: dict[str, Any],
    *,
    max_description_length: int = 2048,
) -> dict[str, Any]:
    """Serialize an MCP tool into a Claude API-compatible tool definition dict.

    The output format is:
        {
            "name": "mcp__<server>__<tool>",
            "description": "...",
            "input_schema": { ... }
        }

    This is suitable for inclusion in the ``tools`` array of a Claude API request.

    Args:
        server_name: MCP server name.
        tool_name: Tool name (unscoped) as returned by the MCP server.
        raw_tool: Raw tool definition dict from ``tools/list``.
        max_description_length: Maximum description length.

    Returns:
        A dict compatible with the Claude API ``tools`` parameter.
    """
    input_schema = raw_tool.get("inputSchema", raw_tool.get("input_schema", {}))
    if isinstance(input_schema, dict):
        input_schema = normalize_input_schema(input_schema)
    else:
        input_schema = {"type": "object", "properties": {}}

    description = str(raw_tool.get("description", ""))[:max_description_length]

    return {
        "name": f"mcp__{server_name}__{tool_name}",
        "description": description,
        "input_schema": input_schema,
    }


def validate_tool_input_schema(schema: dict[str, Any]) -> tuple[bool, str | None]:
    """Lightweight validation of a tool input schema against JSON Schema conventions.

    Returns ``(is_valid, error_message)``.  ``error_message`` is ``None`` when valid.

    Checks performed:
    - Top-level ``type`` must be ``"object"`` (Claude API convention).
    - Required properties (if present) must actually exist in ``properties``.
    - ``properties`` values must be dicts (not primitives).
    """
    if not isinstance(schema, dict):
        return False, "Input schema must be a dict"

    schema_type = schema.get("type")
    if schema_type is not None and schema_type != "object":
        return False, f"Top-level schema type must be 'object', got '{schema_type}'"

    # If type is missing but there are properties, it's acceptable
    if "required" in schema:
        required = schema["required"]
        properties = schema.get("properties", {})
        if isinstance(required, list) and isinstance(properties, dict):
            missing = [r for r in required if r not in properties]
            if missing:
                return False, f"Required properties not in schema: {missing}"

    if "properties" in schema:
        props = schema["properties"]
        if not isinstance(props, dict):
            return False, "Schema 'properties' must be an object"
        for prop_name, prop_schema in props.items():
            if not isinstance(prop_schema, dict):
                return False, (
                    f"Property '{prop_name}' schema must be a dict, "
                    f"got {type(prop_schema).__name__}"
                )

    return True, None


def batch_serialize_tools(
    server_name: str,
    raw_tools: list[dict[str, Any]],
    *,
    max_description_length: int = 2048,
) -> list[dict[str, Any]]:
    """Serialize a list of raw MCP tool definitions for the Claude API.

    Convenience wrapper around ``serialize_tool_for_claude`` for batch processing.
    Filters out tools with empty names.
    """
    result: list[dict[str, Any]] = []
    for raw in raw_tools:
        tool_name = raw.get("name", "")
        if not tool_name:
            continue
        result.append(
            serialize_tool_for_claude(
                server_name,
                tool_name,
                raw,
                max_description_length=max_description_length,
            )
        )
    return result


# ---------------------------------------------------------------------------
# MCP error formatting
# ---------------------------------------------------------------------------
# MCP errors come from multiple layers: JSON-RPC transport, protocol-level
# responses, connection lifecycle, and tool-call results.  Consistent error
# formatting ensures readable messages for end users and predictable error
# shapes for the permission / retry pipelines.


# Standard JSON-RPC error codes (MCP spec + common conventions)
# https://www.jsonrpc.org/specification#error_object
JSONRPC_ERROR_DESCRIPTIONS: dict[int, str] = {
    -32700: "Parse error — invalid JSON was received by the server",
    -32600: "Invalid Request — the JSON sent is not a valid Request object",
    -32601: "Method not found — the method does not exist or is not available",
    -32602: "Invalid params — invalid method parameter(s)",
    -32603: "Internal error — internal JSON-RPC error",
    -32000: "Server error — general server-side error",
    -32001: "Connection closed — the transport connection was lost",
    -32002: "Resource exhausted — server rate limit or capacity exceeded",
    -32003: "Initialization failed — server capabilities negotiation failed",
    -32004: "Tool execution failed — the tool call returned an error",
    -32005: "Authentication required — OAuth or token flow needed",
}


def format_jsonrpc_error(code: int, message: str, data: Any = None) -> dict[str, Any]:
    """Format a JSON-RPC error as a structured dict matching the MCP error spec.

    Returns a dict with ``code``, ``message``, and optionally ``data`` that
    matches the MCP protocol error object shape.
    """
    result: dict[str, Any] = {
        "code": code,
        "message": message,
    }
    if data is not None:
        result["data"] = data
    return result


def format_mcp_error_for_user(
    code: int,
    message: str = "",
    *,
    server_name: str = "",
    tool_name: str = "",
) -> str:
    """Format an MCP error into a human-readable user-facing message.

    Includes the JSON-RPC error description when available and contextual
    server / tool information for debugging.

    Args:
        code: JSON-RPC error code (negative integer per spec).
        message: Error message from the server or transport layer.
        server_name: Optional MCP server name for context.
        tool_name: Optional MCP tool name for context.

    Returns:
        A human-readable error string suitable for display in chat.
    """
    parts: list[str] = []

    # Standard description for known codes
    standard_desc = JSONRPC_ERROR_DESCRIPTIONS.get(code)
    if standard_desc:
        parts.append(standard_desc)
    elif message:
        parts.append(message)
    else:
        parts.append(f"Unknown MCP error (code {code})")

    # Append server-supplied message if it adds context beyond the standard desc
    if message and standard_desc and message.lower() not in standard_desc.lower():
        parts.append(f"Details: {message}")

    # Add server / tool context
    context_parts = []
    if server_name:
        context_parts.append(f"server '{server_name}'")
    if tool_name:
        context_parts.append(f"tool '{tool_name}'")
    if context_parts:
        parts.append("(from " + ", ".join(context_parts) + ")")

    return " ".join(parts)


def is_retryable_mcp_error(code: int, message: str = "") -> bool:
    """Determine whether an MCP error is likely retryable.

    Retryable errors include:
    - Transport / connection failures (codes -32000, -32001)
    - Rate limiting / resource exhaustion (-32002)
    - Timeouts and temporary server unavailability

    Non-retryable errors include:
    - Invalid request / parse errors (-32700, -32600)
    - Method not found (-32601)
    - Invalid params (-32602)
    """
    retryable_codes = frozenset({-32000, -32001, -32002, -32003})
    if code in retryable_codes:
        return True

    # Heuristic: check the message for transient-failure keywords
    lower_msg = message.lower()
    transient_keywords = (
        "timeout",
        "timed out",
        "connection refused",
        "connection reset",
        "temporarily unavailable",
        "rate limit",
        "too many requests",
        "service unavailable",
        "try again",
        "retry",
    )
    for keyword in transient_keywords:
        if keyword in lower_msg:
            return True

    return False


def create_tool_error_result(
    error_message: str,
    *,
    server_name: str = "",
    tool_name: str = "",
    is_error: bool = True,
) -> dict[str, Any]:
    """Create a structured error result dict for a failed MCP tool call.

    This matches the shape returned by ``McpClientPool.call_tool`` on error
    so callers can handle success and failure uniformly.

    Returns a dict with ``content`` (list of text blocks), ``is_error``,
    and ``error`` fields.
    """
    text = error_message
    if server_name and tool_name:
        text = f"MCP tool '{tool_name}' on server '{server_name}' failed: {error_message}"
    elif server_name:
        text = f"MCP server '{server_name}' error: {error_message}"
    elif tool_name:
        text = f"MCP tool '{tool_name}' error: {error_message}"

    return {
        "content": [{"type": "text", "text": text}],
        "is_error": is_error,
        "error": error_message,
    }


def classify_mcp_error(
    code: int,
    message: str = "",
) -> str:
    """Classify an MCP error into a category string for metrics / routing.

    Categories:
    - ``"parse"`` — Invalid JSON / parse errors (-32700)
    - ``"invalid_request"`` — Malformed request (-32600)
    - ``"method_not_found"`` — Unknown method (-32601)
    - ``"invalid_params"`` — Bad parameters (-32602)
    - ``"internal"`` — Internal server error (-32603)
    - ``"transport"`` — Connection / transport failures (-32000, -32001)
    - ``"rate_limit"`` — Resource exhaustion (-32002)
    - ``"auth"`` — Authentication required (-32005)
    - ``"tool_error"`` — Tool execution failure (-32004)
    - ``"unknown"`` — Unrecognized error code
    """
    category_map: dict[int, str] = {
        -32700: "parse",
        -32600: "invalid_request",
        -32601: "method_not_found",
        -32602: "invalid_params",
        -32603: "internal",
        -32000: "transport",
        -32001: "transport",
        -32002: "rate_limit",
        -32003: "transport",
        -32004: "tool_error",
        -32005: "auth",
    }

    if code in category_map:
        return category_map[code]

    # Heuristic fallback based on message content
    lower_msg = message.lower()
    if "auth" in lower_msg or "oauth" in lower_msg or "token" in lower_msg:
        return "auth"
    if "timeout" in lower_msg or "connection" in lower_msg or "transport" in lower_msg:
        return "transport"
    if "rate" in lower_msg or "capacity" in lower_msg or "limit" in lower_msg:
        return "rate_limit"

    return "unknown"


def format_connection_error(
    server_name: str,
    error: Exception | str,
    *,
    transport: str = "stdio",
) -> str:
    """Format a connection-level MCP error into a user-friendly message.

    Covers common failure modes: command not found, permission denied,
    DNS / network failures, authentication errors, and timeouts.

    Args:
        server_name: MCP server name that failed to connect.
        error: The exception or error string from the connection attempt.
        transport: The transport type (``"stdio"``, ``"sse"``, ``"http"``, ``"ws"``, ``"sdk"``).

    Returns:
        A human-readable error string.
    """
    error_str = str(error) if isinstance(error, Exception) else error
    lower = error_str.lower()

    # ---- stdio-specific checks (command not found, permissions) ----
    if transport == "stdio":
        if "no such file" in lower or ("not found" in lower and "name or service" not in lower):
            return (
                f"MCP server '{server_name}' failed to start: command not found. "
                f"Check that the command is installed and in your PATH."
            )
        if "permission denied" in lower:
            return (
                f"MCP server '{server_name}' failed to start: permission denied. "
                f"Ensure the command is executable."
            )

    # ---- transport-agnostic checks (DNS, connection, timeout, auth) ----
    if "name or service not known" in lower or "nodename nor servname" in lower:
        return (
            f"MCP server '{server_name}' could not be reached: DNS resolution failed."
        )
    if "connection refused" in lower:
        return (
            f"MCP server '{server_name}' connection refused. "
            f"Verify the server is running and the URL is correct."
        )
    if "timeout" in lower or "timed out" in lower:
        return (
            f"MCP server '{server_name}' connection timed out. "
            f"The server may be unreachable or slow to respond."
        )
    if "401" in lower or "403" in lower or "unauthorized" in lower:
        return (
            f"MCP server '{server_name}' authentication failed. "
            f"Check your credentials or OAuth token."
        )

    return f"MCP server '{server_name}' connection failed: {error_str}"


def get_mcp_error_help_text(
    code: int,
    *,
    server_name: str = "",
    transport: str = "stdio",
) -> str | None:
    """Return context-sensitive help text for common MCP errors.

    Args:
        code: JSON-RPC error code.
        server_name: Optional server name for context.
        transport: The transport type.

    Returns:
        A help string or ``None`` if no specific guidance is available.
    """
    help_map: dict[int, str] = {
        -32700: (
            "The MCP server returned invalid JSON. This is usually a bug in the "
            "server process. Check the server logs for details."
        ),
        -32601: (
            "The requested MCP method is not supported. The server may be running "
            "an older version of the MCP protocol. Try upgrading the server."
        ),
        -32602: (
            "The tool parameters were rejected by the MCP server. Verify the "
            "argument types match the tool's input schema."
        ),
        -32000: (
            "A transport-level error occurred. For stdio servers, check that the "
            "command and args are correct. For remote servers, verify the URL "
            "and network connectivity."
        ),
        -32001: (
            "The MCP connection was lost. The server process may have crashed "
            "or the network connection was interrupted."
        ),
        -32002: (
            "The MCP server is rate-limited or exhausted. Wait a moment and "
            "try again, or check the server's capacity settings."
        ),
        -32005: (
            "MCP authentication is required. Use `/mcp-auth` to complete OAuth "
            "or provide the necessary credentials."
        ),
    }

    transport_specific: dict[str, str] = {
        "stdio": (
            f"For stdio servers, verify that the command is installed and the "
            f"environment is correctly configured."
        ),
        "sse": (
            f"For SSE servers, verify the URL accepts Server-Sent Events connections."
        ),
        "ws": (
            f"For WebSocket servers, verify the URL starts with ws:// or wss:// "
            f"and the endpoint is accessible."
        ),
    }

    parts: list[str] = []
    generic = help_map.get(code)
    if generic:
        parts.append(generic)

    specific = transport_specific.get(transport)
    if specific:
        parts.append(specific)

    if not parts:
        return None

    if server_name:
        parts.insert(0, f"MCP server '{server_name}' encountered an error:")

    return " ".join(parts)


def extract_error_from_tool_result(
    result: dict[str, Any],
) -> tuple[bool, str]:
    """Extract error information from an MCP tool call result dict.

    Parses the result shape returned by ``McpClientPool.call_tool`` and
    returns ``(is_error, error_message)``.

    Handles:
    - ``is_error`` / ``isError`` flags at the top level.
    - ``error`` string field.
    - MCP content blocks with ``isError: true``.
    - Structured content errors.
    """
    # Top-level error flag
    is_err = bool(result.get("is_error") or result.get("isError", False))

    # Explicit error message field
    error_msg = result.get("error", "")
    if isinstance(error_msg, str) and error_msg:
        return is_err, error_msg

    # Check content blocks for error indicators
    content = result.get("content", [])
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                if block.get("isError", False):
                    text = block.get("text", "")
                    if isinstance(text, str) and text:
                        return True, text

    # Check structuredContent
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        if structured.get("error"):
            return True, str(structured["error"])

    # No error found but is_error flag was set
    if is_err:
        return True, "Unknown MCP tool error"

    return False, ""
