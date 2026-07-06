"""
REPLTool – primitive tools listing.

Port of: src/tools/REPLTool/primitiveTools.ts
"""

from __future__ import annotations

from typing import Any

_primitive_tools: list[Any] | None = None


def get_repl_primitive_tools() -> list[Any]:
    """Lazy-loaded list of primitive tools hidden from direct model use in REPL mode."""
    global _primitive_tools
    if _primitive_tools is None:
        from hare.tools_impl.FileReadTool import FileReadTool
        from hare.tools_impl.FileWriteTool import FileWriteTool
        from hare.tools_impl.FileEditTool import FileEditTool
        from hare.tools_impl.GlobTool import GlobTool
        from hare.tools_impl.GrepTool import GrepTool
        from hare.tools_impl.BashTool import BashTool

        _primitive_tools = [
            FileReadTool,
            FileWriteTool,
            FileEditTool,
            GlobTool,
            GrepTool,
            BashTool,
        ]
    return _primitive_tools
