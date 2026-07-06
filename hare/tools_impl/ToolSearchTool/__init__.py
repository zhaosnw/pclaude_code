"""Port of: src/tools/ToolSearchTool/"""

TOOL_SEARCH_TOOL_NAME = "ToolSearch"

try:
    from hare.tools_impl.ToolSearchTool.prompt import *  # noqa: F401,F403
except ImportError:
    pass
