"""Port of: src/tools/SendMessageTool/"""

SEND_MESSAGE_TOOL_NAME = "SendMessage"

try:
    from hare.tools_impl.SendMessageTool.prompt import *  # noqa: F401,F403
except ImportError:
    pass
