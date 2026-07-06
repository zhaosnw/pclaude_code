"""
LSP client for Language Server Protocol operations.

Port of: src/services/lsp/LSPClient.ts

Provides a client for communicating with LSP servers via subprocess stdio.
Supports definition lookup, references, hover, diagnostics, completions,
document symbols, rename, and code actions.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Optional

# LSP protocol constants
LSP_METHODS = {
    "initialize": "initialize",
    "initialized": "initialized",
    "text_document_did_open": "textDocument/didOpen",
    "text_document_did_close": "textDocument/didClose",
    "text_document_did_change": "textDocument/didChange",
    "definition": "textDocument/definition",
    "references": "textDocument/references",
    "hover": "textDocument/hover",
    "completion": "textDocument/completion",
    "diagnostic": "textDocument/diagnostic",
    "document_symbol": "textDocument/documentSymbol",
    "rename": "textDocument/rename",
    "code_action": "textDocument/codeAction",
    "shutdown": "shutdown",
    "exit": "exit",
}


@dataclass
class LSPClient:
    """Client for communicating with an LSP server process."""

    server_name: str
    language: str = ""
    command: list[str] = field(default_factory=list)
    connected: bool = False
    _process: Optional[asyncio.subprocess.Process] = field(default=None, repr=False)
    _seq: int = field(default=0, repr=False)
    _pending: dict[int, asyncio.Future[Any]] = field(default_factory=dict, repr=False)

    async def connect(self) -> bool:
        """Start the LSP server process and initialize."""
        if not self.command:
            return False
        try:
            self._process = await asyncio.create_subprocess_exec(
                *self.command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            # Send initialize request
            result = await self._send_request(LSP_METHODS["initialize"], {
                "processId": None,
                "rootUri": None,
                "capabilities": {
                    "textDocument": {
                        "definition": {"dynamicRegistration": True},
                        "references": {"dynamicRegistration": True},
                        "hover": {"dynamicRegistration": True},
                        "completion": {"dynamicRegistration": True},
                        "documentSymbol": {"dynamicRegistration": True},
                        "rename": {"dynamicRegistration": True},
                        "codeAction": {"dynamicRegistration": True},
                    }
                },
            })
            if result:
                self.connected = True
            return self.connected
        except Exception:
            return False

    async def disconnect(self) -> None:
        """Shutdown and disconnect the LSP server."""
        if self._process and self.connected:
            try:
                await self._send_request(LSP_METHODS["shutdown"], {})
                await self._send_notification(LSP_METHODS["exit"], {})
            except Exception:
                pass
        if self._process:
            try:
                self._process.kill()
                await self._process.wait()
            except Exception:
                pass
        self.connected = False
        self._process = None

    async def _send_request(self, method: str, params: dict[str, Any]) -> Any:
        """Send a JSON-RPC request and wait for response."""
        if not self._process or not self._process.stdin:
            return None
        self._seq += 1
        msg = {"jsonrpc": "2.0", "id": self._seq, "method": method, "params": params}
        data = json.dumps(msg).encode("utf-8")
        content = f"Content-Length: {len(data)}\r\n\r\n".encode("utf-8") + data
        self._process.stdin.write(content)
        await self._process.stdin.drain()

        future: asyncio.Future[Any] = asyncio.get_event_loop().create_future()
        self._pending[self._seq] = future
        try:
            return await asyncio.wait_for(future, timeout=30)
        except asyncio.TimeoutError:
            self._pending.pop(self._seq, None)
            return None

    async def _send_notification(self, method: str, params: dict[str, Any]) -> None:
        """Send a JSON-RPC notification (no response expected)."""
        if not self._process or not self._process.stdin:
            return
        msg = {"jsonrpc": "2.0", "method": method, "params": params}
        data = json.dumps(msg).encode("utf-8")
        content = f"Content-Length: {len(data)}\r\n\r\n".encode("utf-8") + data
        self._process.stdin.write(content)
        await self._process.stdin.drain()

    async def get_definition(self, file: str, line: int, col: int) -> Optional[dict[str, Any]]:
        """Get definition at position. Returns {uri, range} or None."""
        if not self.connected:
            return None
        return await self._send_request(LSP_METHODS["definition"], {
            "textDocument": {"uri": f"file://{file}"},
            "position": {"line": line, "character": col},
        })

    async def get_references(self, file: str, line: int, col: int) -> list[dict[str, Any]]:
        """Get all references at position."""
        if not self.connected:
            return []
        result = await self._send_request(LSP_METHODS["references"], {
            "textDocument": {"uri": f"file://{file}"},
            "position": {"line": line, "character": col},
            "context": {"includeDeclaration": True},
        })
        return result if isinstance(result, list) else []

    async def get_hover(self, file: str, line: int, col: int) -> Optional[str]:
        """Get hover information at position."""
        if not self.connected:
            return None
        result = await self._send_request(LSP_METHODS["hover"], {
            "textDocument": {"uri": f"file://{file}"},
            "position": {"line": line, "character": col},
        })
        if isinstance(result, dict):
            contents = result.get("contents", {})
            if isinstance(contents, dict):
                return str(contents.get("value", ""))
            if isinstance(contents, str):
                return contents
        return None

    async def get_diagnostics(self, file: str) -> list[dict[str, Any]]:
        """Get diagnostics for a file."""
        if not self.connected:
            return []
        result = await self._send_request(LSP_METHODS["diagnostic"], {
            "textDocument": {"uri": f"file://{file}"},
        })
        if isinstance(result, dict):
            return result.get("items", [])
        return []

    async def get_completions(self, file: str, line: int, col: int) -> list[dict[str, Any]]:
        """Get completion items at position."""
        if not self.connected:
            return []
        result = await self._send_request(LSP_METHODS["completion"], {
            "textDocument": {"uri": f"file://{file}"},
            "position": {"line": line, "character": col},
        })
        if isinstance(result, dict):
            return result.get("items", [])
        if isinstance(result, list):
            return result
        return []

    async def get_document_symbols(self, file: str) -> list[dict[str, Any]]:
        """Get document symbols (outline) for a file."""
        if not self.connected:
            return []
        result = await self._send_request(LSP_METHODS["document_symbol"], {
            "textDocument": {"uri": f"file://{file}"},
        })
        return result if isinstance(result, list) else []

    async def rename(self, file: str, line: int, col: int, new_name: str) -> Optional[dict[str, Any]]:
        """Rename a symbol at position."""
        if not self.connected:
            return None
        return await self._send_request(LSP_METHODS["rename"], {
            "textDocument": {"uri": f"file://{file}"},
            "position": {"line": line, "character": col},
            "newName": new_name,
        })

    async def get_code_actions(self, file: str, start_line: int, start_col: int,
                                end_line: int, end_col: int) -> list[dict[str, Any]]:
        """Get code actions for a range."""
        if not self.connected:
            return []
        result = await self._send_request(LSP_METHODS["code_action"], {
            "textDocument": {"uri": f"file://{file}"},
            "range": {
                "start": {"line": start_line, "character": start_col},
                "end": {"line": end_line, "character": end_col},
            },
            "context": {"diagnostics": []},
        })
        return result if isinstance(result, list) else []

    async def did_open(self, uri: str, language: str, content: str) -> None:
        """Notify server that a document was opened."""
        await self._send_notification(LSP_METHODS["text_document_did_open"], {
            "textDocument": {
                "uri": uri,
                "languageId": language,
                "version": 1,
                "text": content,
            },
        })

    async def did_close(self, uri: str) -> None:
        """Notify server that a document was closed."""
        await self._send_notification(LSP_METHODS["text_document_did_close"], {
            "textDocument": {"uri": uri},
        })
