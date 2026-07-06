"""
Model picker options for UI / settings.

Port of: src/utils/model/modelOptions.ts (subset).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ModelOption:
    value: Any
    label: str
    description: str
    description_for_model: str | None = None


def get_default_option_for_user(fast_mode: bool = False) -> ModelOption:
    del fast_mode
    return ModelOption(
        value=None,
        label="Default (recommended)",
        description="Use the default model",
        description_for_model=None,
    )


def list_model_options_for_user() -> list[ModelOption]:
    return [get_default_option_for_user()]
