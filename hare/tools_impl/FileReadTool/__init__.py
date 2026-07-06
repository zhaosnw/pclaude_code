from hare.tools_impl.FileReadTool.file_read_tool import (
    input_schema as _input_schema,
    call as _call,
    FILE_READ_TOOL_NAME,
    MAX_LINES_TO_READ,
)

# Re-export for backward compatibility
input_schema = _input_schema
call = _call


class FileReadTool:
    """TS-parity class wrapper for FileReadTool."""

    name = "FileRead"

    def input_schema(self):
        return _input_schema()

    async def call(self, args, context, can_use_tool, parent_message, on_progress=None):
        return await _call(args, context, can_use_tool, parent_message, on_progress)
