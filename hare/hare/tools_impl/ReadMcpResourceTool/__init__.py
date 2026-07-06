"""Port of: src/tools/ReadMcpResourceTool/"""

READ_MCP_RESOURCE_TOOL_NAME = "ReadMcpResource"

try:
    from hare.tools_impl.ReadMcpResourceTool.prompt import *  # noqa: F401,F403
except ImportError:
    pass
