"""Port of: src/tools/AgentTool/"""

AGENT_TOOL_NAME = "Agent"

try:
    from hare.tools_impl.AgentTool.prompt import *  # noqa: F401,F403
except ImportError:
    pass
