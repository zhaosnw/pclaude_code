"""Port of: src/tools/ConfigTool/"""

CONFIG_TOOL_NAME = "Config"

try:
    from hare.tools_impl.ConfigTool.prompt import *  # noqa: F401,F403
except ImportError:
    pass
