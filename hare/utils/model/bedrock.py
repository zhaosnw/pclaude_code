"""Port of: src/utils/model/bedrock.ts"""

from __future__ import annotations
import os


def is_bedrock_provider() -> bool:
    return os.environ.get("ANTHROPIC_API_PROVIDER", "").lower() == "bedrock"


def get_bedrock_region() -> str:
    return os.environ.get("AWS_REGION", "us-east-1")
