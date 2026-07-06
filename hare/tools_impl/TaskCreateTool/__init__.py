"""Port of: src/tools/TaskCreateTool/"""

TASK_CREATE_TOOL_NAME = "TaskCreate"

try:
    from hare.tools_impl.TaskCreateTool.prompt import *  # noqa: F401,F403
except ImportError:
    pass
