"""Port of: src/tools/TaskStopTool/"""

TASK_STOP_TOOL_NAME = "TaskStop"

try:
    from hare.tools_impl.TaskStopTool.prompt import *  # noqa: F401,F403
except ImportError:
    pass
