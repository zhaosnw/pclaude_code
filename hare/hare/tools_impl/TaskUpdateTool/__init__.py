"""Port of: src/tools/TaskUpdateTool/"""

TASK_UPDATE_TOOL_NAME = "TaskUpdate"

try:
    from hare.tools_impl.TaskUpdateTool.prompt import *  # noqa: F401,F403
except ImportError:
    pass
