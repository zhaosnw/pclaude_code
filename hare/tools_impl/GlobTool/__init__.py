from hare.tools_impl.GlobTool.glob_tool import (
    input_schema as _input_schema,
    call as _call,
    GLOB_TOOL_NAME,
)

# Re-export for backward compatibility
input_schema = _input_schema
call = _call


class GlobTool:
    """TS-parity class wrapper for GlobTool."""

    name = "Glob"

    def input_schema(self):
        return _input_schema()

    async def call(self, args, context, can_use_tool, parent_message, on_progress=None):
        return await _call(args, context, can_use_tool, parent_message, on_progress)
