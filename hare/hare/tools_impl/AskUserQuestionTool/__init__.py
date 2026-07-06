"""Port of: src/tools/AskUserQuestionTool/"""

ASK_USER_QUESTION_TOOL_NAME = "AskUserQuestion"

try:
    from hare.tools_impl.AskUserQuestionTool.prompt import *  # noqa: F401,F403
except ImportError:
    pass
