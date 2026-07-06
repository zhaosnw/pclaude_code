"""Port of: src/tools/SleepTool/"""

SLEEP_TOOL_NAME = "Sleep"

try:
    from hare.tools_impl.SleepTool.prompt import *  # noqa: F401,F403
except ImportError:
    pass
