from hare.tools_impl.FileEditTool.file_edit_tool import (
    input_schema as _input_schema,
    call as _call,
    FILE_EDIT_TOOL_NAME,
)

# Re-export for backward compatibility
input_schema = _input_schema
call = _call


class FileEditTool:
    """TS-parity class wrapper for FileEditTool."""

    name = "FileEdit"

    def input_schema(self):
        return _input_schema()

    async def call(self, args, context, can_use_tool, parent_message, on_progress=None):
        return await _call(args, context, can_use_tool, parent_message, on_progress)
