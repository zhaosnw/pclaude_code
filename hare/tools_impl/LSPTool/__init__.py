"""Port of: src/tools/LSPTool/"""

LSP_TOOL_NAME = "LSP"

try:
    from hare.tools_impl.LSPTool.prompt import *  # noqa: F401,F403
except ImportError:
    pass
