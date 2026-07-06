"""Port of: src/tools/BriefTool/"""

BRIEF_TOOL_NAME = "Brief"

try:
    from hare.tools_impl.BriefTool.prompt import *  # noqa: F401,F403
except ImportError:
    pass
