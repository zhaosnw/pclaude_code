"""Port of: src/tools/TaskGetTool/"""

TASK_GET_TOOL_NAME = "TaskGet"

try:
    from hare.tools_impl.TaskGetTool.prompt import *  # noqa: F401,F403
except ImportError:
    pass
