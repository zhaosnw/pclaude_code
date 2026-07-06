"""known_marketplaces.json and marketplace fetch. Port of marketplaceManager.ts."""

from __future__ import annotations

from typing import Any


async def load_known_marketplaces_config_safe() -> dict[str, Any]:
    return {}


async def load_known_marketplaces_config() -> dict[str, Any]:
    return {}


async def get_marketplace(_name: str) -> dict[str, Any]:
    return {"name": _name, "plugins": [], "owner": {"name": ""}}


async def add_marketplace_source(_name: str, _source: Any) -> None:
    pass


def get_declared_marketplaces() -> dict[str, Any]:
    return {}
