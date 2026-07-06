"""
MCPTool – invoke MCP server tools.

Port of: src/tools/MCPTool/MCPTool.ts

Provides access to Model Context Protocol (MCP) tools from connected
MCP servers. Each MCP tool is identified by a fully-qualified name of
the form ``mcp__<server_name>__<tool_name>``. On invocation the name is
parsed to locate the server session, then the JSON-RPC ``tools/call``
method is dispatched against that session.

Content results (text, image, resource blocks) are aggregated and
truncated when they exceed the configured token budget. Binary image
data is persisted to the tool-results directory via the storage helpers.
"""

from __future__ import annotations

import json
from typing import Any

from hare.utils.log import log_error
from hare.utils.mcp_output_storage import (
    get_binary_blob_saved_message,
    persist_binary_content,
)
from hare.utils.mcp_utils import parse_mcp_tool_name
from hare.utils.mcp_validation import truncate_mcp_content_if_needed

MCP_TOOL_NAME = "MCPTool"
TOOL_NAME = "MCPTool"
SEARCH_HINT = "invoke tools from connected MCP servers"


# ---------------------------------------------------------------------------
# Tool interface (wrapped by hare.tools._wrap_module_tool)
# ---------------------------------------------------------------------------


def input_schema() -> dict[str, Any]:
    """Return the base input schema for MCP tools.

    The concrete schema is determined at runtime from each MCP server's
    tool definition. This base schema accepts a ``server_name``,
    ``tool_name``, and ``arguments``, plus any additional properties so
    that parameter shapes from all servers pass through validation.
    """
    return {
        "type": "object",
        "properties": {
            "server_name": {
                "type": "string",
                "description": "MCP server name (e.g. 'github', 'slack').",
            },
            "tool_name": {
                "type": "string",
                "description": "Tool name registered on the MCP server.",
            },
            "arguments": {
                "type": "object",
                "description": "Named arguments forwarded to the MCP tool.",
            },
        },
        "required": ["server_name", "tool_name"],
        "additionalProperties": True,
    }


def is_read_only(input: dict[str, Any]) -> bool:
    """MCP tools default to read-write at this layer.

    Individual tools are annotated per their MCP server definitions;
    the annotations drive the permission pipeline separately.
    """
    return False


def is_destructive(input: dict[str, Any]) -> bool:
    """MCP tools default to non-destructive at this layer."""
    return False


def is_concurrency_safe(input: dict[str, Any]) -> bool:
    """MCP tool calls are not safe to run concurrently with other tools."""
    return False


async def call(
    server_name: str = "",
    tool_name: str = "",
    arguments: dict[str, Any] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Invoke a tool on an MCP server via JSON-RPC ``tools/call``.

    Parameters
    ----------
    server_name:
        Name of the configured MCP server (e.g. ``"github"``).
    tool_name:
        Unqualified tool name registered on that server
        (e.g. ``"search_repositories"``). When the name is
        fully-qualified (``mcp__<server>__<tool>``) it is parsed to
        extract both parts automatically.
    arguments:
        Named arguments forwarded to the MCP server as the JSON-RPC
        params payload.
    **kwargs:
        Additional overrides – reserved for caller-provided MCP client
        injection and future protocol extensions.

    Returns
    -------
    dict
        A result dict with keys ``content`` (str), ``is_error`` (bool),
        and optionally ``structuredContent`` (Any) and/or ``error`` (str).
    """
    # Resolve the actual server and tool name.  The ``tool_name``
    # parameter may already be the unqualified name, or it may carry
    # the ``mcp__<server>__<tool>`` prefix.  Handle both forms.
    parsed = parse_mcp_tool_name(tool_name)
    if parsed is not None:
        resolved_server = parsed[0]
        resolved_tool = parsed[1]
    else:
        resolved_server = server_name
        resolved_tool = tool_name

    if not resolved_server or not resolved_tool:
        return {
            "content": (
                "MCPTool error: server_name and tool_name are required."
                f" Received server='{server_name}', tool='{tool_name}'."
            ),
            "is_error": True,
        }

    # Obtain the MCP client pool.  Allow callers to inject a client via
    # kwargs for testing (``_mcp_client`` / ``mcp_client``).
    mcp_client = kwargs.get("_mcp_client") or kwargs.get("mcp_client")
    if mcp_client is None:
        from hare.services.mcp.client import get_mcp_client_pool

        mcp_client = get_mcp_client_pool()

    # ------------------------------------------------------------------
    # Call the MCP server
    # ------------------------------------------------------------------
    from hare.services.mcp.client import MCPError

    try:
        # Optional pre-flight: ensure the server is connected.  The
        # pool's ``call_tool`` does this internally, but a fast-path
        # check gives a clearer error.
        if not getattr(mcp_client, "is_connected", lambda _: True)(resolved_server):
            return {
                "content": (
                    f"MCP error: not connected to server '{resolved_server}'."
                    " Start the server or check its configuration."
                ),
                "is_error": True,
                "error": f"Server '{resolved_server}' not connected.",
            }

        result = await mcp_client.call_tool(
            resolved_server, resolved_tool, arguments or {}
        )
    except MCPError as exc:
        log_error(exc)
        return {
            "content": f"MCP error [{exc.code}]: {exc.message}",
            "is_error": True,
            "error": exc.message,
        }
    except Exception as exc:
        log_error(exc)
        return {
            "content": str(exc),
            "is_error": True,
            "error": str(exc),
        }

    # ------------------------------------------------------------------
    # Process MCP content blocks (text, image, resource)
    # ------------------------------------------------------------------
    is_error = bool(result.get("is_error", False))
    structured_content = result.get("structuredContent")
    content = result.get("content", [])

    output_parts: list[str] = []
    binary_info: list[str] = []

    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type", "text")

            if block_type == "text":
                t = block.get("text", "")
                if t:
                    output_parts.append(t)

            elif block_type == "image":
                data = block.get("data", "")
                mime_type = block.get("mimeType", "")
                if data and mime_type:
                    try:
                        import base64
                        import hashlib

                        img_bytes = base64.b64decode(data)
                        content_id = hashlib.sha256(img_bytes).hexdigest()[:16]
                        persisted = persist_binary_content(
                            img_bytes, mime_type, content_id
                        )
                        if (
                            isinstance(persisted, dict)
                            and "filepath" in persisted
                        ):
                            binary_info.append(
                                get_binary_blob_saved_message(
                                    persisted["filepath"],
                                    mime_type,
                                    persisted.get("size", 0),
                                    f"Image from {resolved_server}/{resolved_tool}: ",
                                )
                            )
                    except Exception as exc:
                        log_error(exc)
                        binary_info.append(
                            f"[Image: {mime_type}, base64-encoded]"
                        )
                else:
                    binary_info.append("[Image (no data)]")

            elif block_type == "resource":
                resource = block.get("resource", {})
                if isinstance(resource, dict):
                    if resource.get("text"):
                        output_parts.append(str(resource["text"]))
                    elif resource.get("blob"):
                        binary_info.append(
                            f"[Binary resource: {resource.get('uri', 'unknown')}]"
                        )

    # Fall back to structured content when there is no text output
    if not output_parts:
        if structured_content is not None:
            try:
                output_parts.append(json.dumps(structured_content, indent=2))
            except (TypeError, ValueError):
                output_parts.append(str(structured_content))

    if binary_info:
        output_parts.extend(binary_info)

    output_text = "\n".join(output_parts) if output_parts else "(empty result)"

    # ------------------------------------------------------------------
    # Truncate large content
    # ------------------------------------------------------------------
    try:
        truncated = await truncate_mcp_content_if_needed(output_text)
        final_content = truncated if isinstance(truncated, str) else output_text
    except Exception:
        final_content = output_text

    response: dict[str, Any] = {
        "content": final_content,
        "is_error": is_error,
    }
    if structured_content is not None:
        response["structuredContent"] = structured_content

    return response
