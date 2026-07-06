"""Port of: src/tools/ListMcpResourcesTool/"""

LIST_MCP_RESOURCES_TOOL_NAME = "ListMcpResources"

try:
    from hare.tools_impl.ListMcpResourcesTool.prompt import *  # noqa: F401,F403
except ImportError:
    pass
