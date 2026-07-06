"""Port of: src/tools/FileWriteTool/prompt.ts"""

FILE_WRITE_TOOL_NAME = "Write"
DESCRIPTION = "Write a file to the local filesystem."

FILE_READ_TOOL_NAME = "Read"


def _get_pre_read_instruction() -> str:
    return (
        f"\n- If this is an existing file, you MUST use the {FILE_READ_TOOL_NAME} "
        "tool first to read the file's contents. This tool will fail if you did not "
        "read the file first."
    )


def get_write_tool_description() -> str:
    return f"""\
Writes a file to the local filesystem.

Usage:
- This tool will overwrite the existing file if there is one at the provided path.{_get_pre_read_instruction()}
- Prefer the Edit tool for modifying existing files \u2014 it only sends the diff. Only use this tool to create new files or for complete rewrites.
- NEVER create documentation files (*.md) or README files unless explicitly requested by the User.
- Only use emojis if the user explicitly requests it. Avoid writing emojis to files unless asked."""
