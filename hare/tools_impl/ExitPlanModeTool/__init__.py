"""Port of: src/tools/ExitPlanModeTool/"""

EXIT_PLAN_MODE_TOOL_NAME = "ExitPlanMode"

try:
    from hare.tools_impl.ExitPlanModeTool.prompt import *  # noqa: F401,F403
except ImportError:
    pass
