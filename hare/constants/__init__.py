"""Constants package. Port of: src/constants/"""

from hare.constants.product import PRODUCT_NAME, VERSION, PACKAGE_URL
from hare.constants.figures import FIGURES
from hare.constants.prompts import (
    get_system_prompt,
    get_tool_use_system_prompt,
    SYSTEM_PROMPT_DYNAMIC_BOUNDARY,
)

# Compatibility aliases for modules that import the older constant-style API.
# The full prompt is session-dependent in Python, so expose stable symbolic
# defaults instead of importing missing TS-era constants.
SYSTEM_PROMPT = ""
IDENTITY_PROMPT = ""

__all__ = [
    "PRODUCT_NAME",
    "VERSION",
    "PACKAGE_URL",
    "FIGURES",
    "SYSTEM_PROMPT",
    "IDENTITY_PROMPT",
    "SYSTEM_PROMPT_DYNAMIC_BOUNDARY",
    "get_system_prompt",
    "get_tool_use_system_prompt",
]
