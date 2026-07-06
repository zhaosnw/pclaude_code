"""Port of: src/tools/SkillTool/"""

SKILL_TOOL_NAME = "Skill"

try:
    from hare.tools_impl.SkillTool.prompt import *  # noqa: F401,F403
except ImportError:
    pass
