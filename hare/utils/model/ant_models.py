"""Ant-internal default model helpers.

Port of: src/utils/model/antModels.ts
"""

from __future__ import annotations

import os


def is_ant_user() -> bool:
    return os.environ.get("USER_TYPE") == "ant"


def get_ant_default_model_hint() -> str | None:
    return None
