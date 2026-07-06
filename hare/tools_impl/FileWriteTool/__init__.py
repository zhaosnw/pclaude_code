from hare.tools_impl.FileWriteTool.file_write_tool import (
    input_schema as _input_schema,
    call as _call,
    FILE_WRITE_TOOL_NAME,
)

# Re-export for backward compatibility
input_schema = _input_schema
call = _call


class FileWriteTool:
    """TS-parity class wrapper for FileWriteTool."""

    name = "FileWrite"

    def input_schema(self):
        return _input_schema()

    async def call(self, args, context, can_use_tool, parent_message, on_progress=None):
        return await _call(args, context, can_use_tool, parent_message, on_progress)
