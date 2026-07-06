"""Load LSP servers from plugin manifests. Port of lspPluginIntegration.ts."""

from __future__ import annotations

from typing import Any


async def load_plugin_lsp_servers(_plugin_root: str) -> list[dict[str, Any]]:
    return []
