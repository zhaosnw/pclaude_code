"""
MCP add-server command helpers — parse args, validate transport, add config.

Port of: src/commands/mcp/addCommand.ts (280 lines)
"""

from __future__ import annotations

from typing import Any


def validate_mcp_add_args(
    name: str = "",
    command_or_url: str = "",
    args: list[str] | None = None,
    transport: str | None = None,
    scope: str = "local",
    headers: list[str] | None = None,
    env_vars: list[str] | None = None,
    client_id: str | None = None,
    client_secret: bool = False,
    callback_port: int | None = None,
    xaa: bool = False,
) -> dict[str, Any]:
    """Validate 'claude mcp add' arguments. Returns validated config dict.

    Raises ValueError on invalid input.
    """
    if not name:
        raise ValueError(
            "Server name is required.\nUsage: claude mcp add <name> <command> [args...]"
        )
    if not command_or_url:
        raise ValueError(
            "Command is required when server name is provided.\nUsage: claude mcp add <name> <command> [args...]"
        )

    # Resolve transport
    resolved_transport = transport or "stdio"
    transport_explicit = transport is not None

    # Detect URL-like input
    looks_like_url = command_or_url.startswith(
        ("http://", "https://", "localhost")
    ) or command_or_url.endswith(("/sse", "/mcp"))

    # XAA validation
    if xaa:
        import os

        if not os.environ.get("CLAUDE_CODE_ENABLE_XAA"):
            raise ValueError(
                "Error: --xaa requires CLAUDE_CODE_ENABLE_XAA=1 in your environment"
            )
        missing = []
        if not client_id:
            missing.append("--client-id")
        if not client_secret:
            missing.append("--client-secret")
        if missing:
            raise ValueError(f"Error: --xaa requires: {', '.join(missing)}")

    # Transport-specific
    oauth: dict[str, Any] | None = None
    if resolved_transport in ("sse", "http"):
        if client_id or callback_port or xaa:
            oauth = {}
            if client_id:
                oauth["clientId"] = client_id
            if callback_port:
                oauth["callbackPort"] = callback_port
            if xaa:
                oauth["xaa"] = True

        if client_secret and not client_id:
            raise ValueError("--client-secret requires --client-id")

    elif resolved_transport == "stdio":
        if client_id or client_secret or callback_port or xaa:
            print(
                "Warning: --client-id, --client-secret, --callback-port, and --xaa are only supported for HTTP/SSE transports and will be ignored for stdio.",
                flush=True,
            )

        if not transport_explicit and looks_like_url:
            print(
                f'\nWarning: The command "{command_or_url}" looks like a URL, but is being interpreted as a stdio server as --transport was not specified.',
                flush=True,
            )
            print(
                f"If this is an HTTP server, use: claude mcp add --transport http {name} {command_or_url}",
                flush=True,
            )
            print(
                f"If this is an SSE server, use: claude mcp add --transport sse {name} {command_or_url}",
                flush=True,
            )

    # Parse headers
    parsed_headers: dict[str, str] | None = None
    if headers:
        parsed_headers = {}
        for h in headers:
            if ":" in h:
                k, v = h.split(":", 1)
                parsed_headers[k.strip()] = v.strip()

    # Parse env vars
    parsed_env: dict[str, str] | None = None
    if env_vars:
        parsed_env = {}
        for e in env_vars:
            if "=" in e:
                k, v = e.split("=", 1)
                parsed_env[k.strip()] = v.strip()

    return {
        "name": name,
        "transport": resolved_transport,
        "scope": scope,
        "url": command_or_url if resolved_transport in ("sse", "http") else None,
        "command": command_or_url if resolved_transport == "stdio" else None,
        "args": args or [],
        "headers": parsed_headers,
        "env": parsed_env,
        "oauth": oauth,
        "transport_explicit": transport_explicit,
        "looks_like_url": looks_like_url,
    }
