"""Port of: src/tools/ExitWorktreeTool/"""

EXIT_WORKTREE_TOOL_NAME = "ExitWorktree"

try:
    from hare.tools_impl.ExitWorktreeTool.prompt import *  # noqa: F401,F403
except ImportError:
    pass
