"""Port of: src/tools/NotebookEditTool/"""

NOTEBOOK_EDIT_TOOL_NAME = "NotebookEdit"

try:
    from hare.tools_impl.NotebookEditTool.prompt import *  # noqa: F401,F403
except ImportError:
    pass
