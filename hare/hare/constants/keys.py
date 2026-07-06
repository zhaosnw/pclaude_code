"""Port of: src/constants/keys.ts"""

from __future__ import annotations
import os


def get_growthbook_client_key() -> str:
    return os.environ.get("GROWTHBOOK_CLIENT_KEY", "")


def get_datadog_client_token() -> str:
    return os.environ.get("DD_CLIENT_TOKEN", "")
