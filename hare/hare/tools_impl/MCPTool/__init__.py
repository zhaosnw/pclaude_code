"""Port of: src/tools/MCPTool/"""

MCP_TOOL_NAME = "MCPTool"

try:
    from hare.tools_impl.MCPTool.prompt import *  # noqa: F401,F403
except ImportError:
    pass
