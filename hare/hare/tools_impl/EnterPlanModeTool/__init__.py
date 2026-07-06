"""Port of: src/tools/EnterPlanModeTool/"""

ENTER_PLAN_MODE_TOOL_NAME = "EnterPlanMode"

try:
    from hare.tools_impl.EnterPlanModeTool.prompt import *  # noqa: F401,F403
except ImportError:
    pass
