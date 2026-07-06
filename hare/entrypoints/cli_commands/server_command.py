"""
CLI server command – start API server mode.

Port of: src/entrypoints/cli/serverCommand.ts
"""

from __future__ import annotations


async def run_server_command(
    host: str = "127.0.0.1",
    port: int = 0,
) -> None:
    """Start the HTTP API server."""
    from hare.server.http_server import run_server_async

    server, actual_port = await run_server_async(host, port)
    print(f"Server running on http://{host}:{actual_port}")
    try:
        import asyncio

        await asyncio.Event().wait()
    except KeyboardInterrupt:
        server.shutdown()
