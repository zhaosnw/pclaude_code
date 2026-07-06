"""Port of: src/utils/model/agent.ts"""

from __future__ import annotations
from hare.utils.model.model_full import (
    get_default_haiku_model,
    get_default_sonnet_model,
)


def get_agent_model(agent_type: str = "", parent_model: str = "") -> str:
    if agent_type in ("Explore", "Plan"):
        return get_default_haiku_model()
    return parent_model or get_default_sonnet_model()
