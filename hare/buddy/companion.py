"""Port of: src/buddy/companion.ts — deterministic companion rolls."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Union

from hare.utils.config import get_global_config

from .types import (
    RARITIES,
    RARITY_WEIGHTS,
    STAT_NAMES,
    Companion,
    CompanionBones,
    HATS,
    StoredCompanion,
)

SALT = "friend-2026-401"


def mulberry32(seed: int):
    a = seed & 0xFFFFFFFF

    def _rng() -> float:
        nonlocal a
        a = (a + 0x6D2B79F5) & 0xFFFFFFFF
        t = (a ^ (a >> 15)) * (1 | a)
        t = (t + ((t ^ (t >> 7)) * (61 | t))) ^ t
        return ((t ^ (t >> 14)) & 0xFFFFFFFF) / 4294967296.0

    return _rng


def hash_string(s: str) -> int:
    h = 2166136261
    for ch in s:
        h ^= ord(ch)
        h = (h * 16777619) & 0xFFFFFFFF
    return h


def _pick(rng, arr: Union[tuple[str, ...], list[str]]) -> str:
    return arr[int(rng() * len(arr))]


def roll_rarity(rng) -> str:
    total = sum(RARITY_WEIGHTS.values())
    roll = rng() * total
    for r in RARITIES:
        roll -= RARITY_WEIGHTS[r]
        if roll < 0:
            return r
    return "common"


RARITY_FLOOR: dict[str, int] = {
    "common": 5,
    "uncommon": 15,
    "rare": 25,
    "epic": 35,
    "legendary": 50,
}


def roll_stats(rng, rarity: str) -> dict[str, int]:
    floor = RARITY_FLOOR[rarity]
    peak = _pick(rng, STAT_NAMES)
    dump = _pick(rng, STAT_NAMES)
    while dump == peak:
        dump = _pick(rng, STAT_NAMES)
    stats: dict[str, int] = {}
    for name in STAT_NAMES:
        if name == peak:
            stats[name] = min(100, floor + 50 + int(rng() * 30))
        elif name == dump:
            stats[name] = max(1, floor - 10 + int(rng() * 15))
        else:
            stats[name] = floor + int(rng() * 40)
    return stats


@dataclass
class Roll:
    bones: CompanionBones
    inspiration_seed: int


def roll_from(rng) -> Roll:
    rarity = roll_rarity(rng)
    from .types import EYES, SPECIES

    hat = "none" if rarity == "common" else _pick(rng, list(HATS))
    bones = CompanionBones(
        rarity=rarity,
        species=_pick(rng, SPECIES),
        eye=_pick(rng, EYES),
        hat=hat,
        shiny=rng() < 0.01,
        stats=roll_stats(rng, rarity),
    )
    return Roll(bones=bones, inspiration_seed=int(rng() * 1e9))


_roll_cache: dict[str, Roll] = {}


def roll(user_id: str) -> Roll:
    key = user_id + SALT
    if key in _roll_cache:
        return _roll_cache[key]
    value = roll_from(mulberry32(hash_string(key)))
    _roll_cache[key] = value
    return value


def roll_with_seed(seed: str) -> Roll:
    return roll_from(mulberry32(hash_string(seed)))


def companion_user_id() -> str:
    cfg = get_global_config()
    oa: Any = cfg.oauth_account
    if isinstance(oa, dict):
        u = oa.get("accountUuid")
        if u:
            return str(u)
    elif oa is not None:
        u = getattr(oa, "account_uuid", None) or getattr(oa, "accountUuid", None)
        if u:
            return str(u)
    if cfg.user_id:
        return str(cfg.user_id)
    return "anon"


def _parse_stored(raw: Any) -> Optional[StoredCompanion]:
    if raw is None:
        return None
    if isinstance(raw, StoredCompanion):
        return raw
    if isinstance(raw, dict):
        return StoredCompanion(
            name=str(raw.get("name", "")),
            personality=str(raw.get("personality", "")),
            hatched_at=int(raw.get("hatchedAt", 0)),
        )
    return None


def get_companion() -> Optional[Companion]:
    cfg = get_global_config()
    stored = _parse_stored(cfg.companion)
    if not stored:
        return None
    bones = roll(companion_user_id()).bones
    return Companion(
        rarity=bones.rarity,
        species=bones.species,
        eye=bones.eye,
        hat=bones.hat,
        shiny=bones.shiny,
        stats=bones.stats,
        name=stored.name,
        personality=stored.personality,
        hatched_at=stored.hatched_at,
    )
