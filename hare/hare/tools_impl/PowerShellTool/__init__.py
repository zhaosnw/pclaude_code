"""Port of: src/tools/PowerShellTool/"""

POWERSHELL_TOOL_NAME = "PowerShell"

try:
    from hare.tools_impl.PowerShellTool.prompt import *  # noqa: F401,F403
except ImportError:
    pass


class PowerShellTool:
    """TS-parity class wrapper for PowerShellTool (P2 — stub)."""

    name = "PowerShell"

    def input_schema(self) -> dict:
        from hare.tools_impl.PowerShellTool.powershell_tool import input_schema

        return input_schema()

    async def call(self, args, context, can_use_tool, parent_message, on_progress=None):
        from hare.tools_impl.PowerShellTool.powershell_tool import call as _call

        return await _call(args, context, can_use_tool, parent_message, on_progress)
