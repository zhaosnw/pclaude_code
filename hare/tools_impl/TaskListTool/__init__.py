"""Port of: src/tools/TaskListTool/"""

TASK_LIST_TOOL_NAME = "TaskList"

try:
    from hare.tools_impl.TaskListTool.prompt import *  # noqa: F401,F403
except ImportError:
    pass
