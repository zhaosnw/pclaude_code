"""Port of: src/tools/EnterWorktreeTool/"""

ENTER_WORKTREE_TOOL_NAME = "EnterWorktree"

try:
    from hare.tools_impl.EnterWorktreeTool.prompt import *  # noqa: F401,F403
except ImportError:
    pass
