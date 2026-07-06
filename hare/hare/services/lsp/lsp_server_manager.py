"""
LSP server manager — manage LSP server lifecycle across files.

Port of: src/services/lsp/LSPServerManager.ts + manager.ts
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from hare.services.lsp.lsp_client import LSPClient


@dataclass
class ServerConfig:
    """Configuration for an LSP server."""
    name: str
    command: list[str]
    language: str = ""
    filetypes: list[str] = field(default_factory=list)
    root_patterns: list[str] = field(default_factory=list)
    enabled: bool = True


@dataclass
class LSPServerManager:
    """Manages LSP server instances across multiple languages."""

    _clients: dict[str, LSPClient] = field(default_factory=dict)
    _configs: dict[str, ServerConfig] = field(default_factory=dict)
    _filetype_map: dict[str, str] = field(default_factory=dict)  # file_ext -> server_name

    def register_server(self, config: ServerConfig) -> None:
        """Register an LSP server configuration."""
        self._configs[config.name] = config
        for ft in config.filetypes:
            self._filetype_map[ft] = config.name

    def unregister_server(self, server_name: str) -> None:
        """Remove an LSP server."""
        self._configs.pop(server_name, None)
        self._filetype_map = {k: v for k, v in self._filetype_map.items() if v != server_name}

    async def get_client(self, server_name: str) -> Optional[LSPClient]:
        """Get or create a client for a server."""
        if server_name in self._clients:
            return self._clients[server_name]
        return await self.start_server(server_name)

    async def get_client_for_file(self, filepath: str) -> Optional[LSPClient]:
        """Get the appropriate LSP client based on file extension."""
        import os
        ext = os.path.splitext(filepath)[1].lstrip(".")
        if ext in self._filetype_map:
            server_name = self._filetype_map[ext]
            return await self.get_client(server_name)

        # Try full filename match
        basename = os.path.basename(filepath)
        for config in self._configs.values():
            for pattern in config.root_patterns:
                if pattern == basename:
                    return await self.get_client(config.name)
        return None

    async def start_server(self, server_name: str) -> Optional[LSPClient]:
        """Start an LSP server and create a client."""
        config = self._configs.get(server_name)
        if not config or not config.enabled:
            return None

        client = LSPClient(
            server_name=config.name,
            language=config.language,
            command=config.command,
        )
        success = await client.connect()
        if success:
            self._clients[server_name] = client
            return client
        return None

    async def stop_server(self, server_name: str) -> None:
        """Stop an LSP server."""
        client = self._clients.pop(server_name, None)
        if client:
            await client.disconnect()

    async def stop_all(self) -> None:
        """Stop all LSP servers."""
        for client in list(self._clients.values()):
            await client.disconnect()
        self._clients.clear()

    def list_servers(self) -> list[dict[str, Any]]:
        """List all servers with status."""
        result = []
        for name, config in self._configs.items():
            client = self._clients.get(name)
            result.append({
                "name": name,
                "language": config.language,
                "filetypes": config.filetypes,
                "connected": client.connected if client else False,
                "enabled": config.enabled,
            })
        return result

    def is_server_running(self, server_name: str) -> bool:
        client = self._clients.get(server_name)
        return client is not None and client.connected

    def get_default_configs(self) -> list[ServerConfig]:
        """Get commonly used LSP server configs for auto-detection."""
        return [
            ServerConfig(
                name="pylsp", language="python",
                command=["pylsp"], filetypes=["py"], root_patterns=["pyproject.toml", "setup.py"],
            ),
            ServerConfig(
                name="typescript-language-server", language="typescript",
                command=["typescript-language-server", "--stdio"], filetypes=["ts", "tsx", "js", "jsx"],
                root_patterns=["tsconfig.json", "package.json"],
            ),
            ServerConfig(
                name="gopls", language="go",
                command=["gopls"], filetypes=["go"], root_patterns=["go.mod"],
            ),
            ServerConfig(
                name="rust-analyzer", language="rust",
                command=["rust-analyzer"], filetypes=["rs"], root_patterns=["Cargo.toml"],
            ),
        ]
