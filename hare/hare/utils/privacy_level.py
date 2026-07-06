"""
Privacy / telemetry traffic levels. Port of src/utils/privacyLevel.ts.
"""

from __future__ import annotations

import os
from typing import Literal

PrivacyLevel = Literal["default", "no-telemetry", "essential-traffic"]


def get_privacy_level() -> PrivacyLevel:
    if os.environ.get("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"):
        return "essential-traffic"
    if os.environ.get("DISABLE_TELEMETRY"):
        return "no-telemetry"
    return "default"


def is_essential_traffic_only() -> bool:
    return get_privacy_level() == "essential-traffic"


def is_telemetry_disabled() -> bool:
    return get_privacy_level() != "default"


def get_essential_traffic_only_reason() -> str | None:
    if os.environ.get("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"):
        return "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"
    return None
