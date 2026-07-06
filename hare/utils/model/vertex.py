"""Port of: src/utils/model/vertex.ts"""

from __future__ import annotations
import os


def is_vertex_provider() -> bool:
    return os.environ.get("ANTHROPIC_API_PROVIDER", "").lower() == "vertex"


def get_vertex_project() -> str:
    return os.environ.get("GOOGLE_CLOUD_PROJECT", "")
