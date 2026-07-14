#!/usr/bin/env python3
"""Minimal deterministic stdio MCP server for alignment cases.

It implements the smallest protocol surface needed by the MCP coverage axis:
``initialize``, ``tools/list``, and ``tools/call`` for one ``echo`` tool.
Every response is newline-delimited JSON-RPC, with no diagnostic stdout noise.
"""

from __future__ import annotations

import json
import sys
from typing import Any


def respond(request_id: Any, result: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}) + "\n")
    sys.stdout.flush()


for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        message = json.loads(line)
    except json.JSONDecodeError:
        continue

    method = message.get("method", "")
    request_id = message.get("id")
    if request_id is None:
        continue
    if method == "initialize":
        respond(
            request_id,
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "alignment-echo", "version": "1.0"},
            },
        )
    elif method == "tools/list":
        respond(
            request_id,
            {
                "tools": [
                    {
                        "name": "echo",
                        "description": "Echoes the supplied text.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"text": {"type": "string"}},
                            "required": ["text"],
                        },
                    }
                ]
            },
        )
    elif method == "tools/call":
        arguments = message.get("params", {}).get("arguments", {})
        # Land a marker in the sandbox. The scripted fixture makes both sides
        # emit the same result text whether or not the tool actually ran, so
        # this file is the only proof the invocation reached the server.
        with open("mcp_echo_called.txt", "w", encoding="utf-8") as handle:
            handle.write(json.dumps(arguments, sort_keys=True) + "\n")
        respond(request_id, {"content": [{"type": "text", "text": json.dumps(arguments)}]})
    elif method == "resources/list":
        respond(request_id, {"resources": []})
    else:
        respond(request_id, {})
