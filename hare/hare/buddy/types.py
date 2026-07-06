"""Port of: src/buddy/types.ts — companion species, rarities, and bone types."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

RARITIES: tuple[str, ...] = ("common", "uncommon", "rare", "epic", "legendary")
Rarity = Literal["common", "uncommon", "rare", "epic", "legendary"]


def _chars(*codes: int) -> str:
    return "".join(chr(x) for x in codes)


duck = _chars(0x64, 0x75, 0x63, 0x6B)
goose = _chars(0x67, 0x6F, 0x6F, 0x73, 0x65)
blob = _chars(0x62, 0x6C, 0x6F, 0x62)
cat = _chars(0x63, 0x61, 0x74)
dragon = _chars(0x64, 0x72, 0x61, 0x67, 0x6F, 0x6E)
octopus = _chars(0x6F, 0x63, 0x74, 0x6F, 0x70, 0x75, 0x73)
owl = _chars(0x6F, 0x77, 0x6C)
penguin = _chars(0x70, 0x65, 0x6E, 0x67, 0x75, 0x69, 0x6E)
turtle = _chars(0x74, 0x75, 0x72, 0x74, 0x6C, 0x65)
snail = _chars(0x73, 0x6E, 0x61, 0x69, 0x6C)
ghost = _chars(0x67, 0x68, 0x6F, 0x73, 0x74)
axolotl = _chars(0x61, 0x78, 0x6F, 0x6C, 0x6F, 0x74, 0x6C)
capybara = _chars(0x63, 0x61, 0x70, 0x79, 0x62, 0x61, 0x72, 0x61)
cactus = _chars(0x63, 0x61, 0x63, 0x74, 0x75, 0x73)
robot = _chars(0x72, 0x6F, 0x62, 0x6F, 0x74)
rabbit = _chars(0x72, 0x61, 0x62, 0x62, 0x69, 0x74)
mushroom = _chars(0x6D, 0x75, 0x73, 0x68, 0x72, 0x6F, 0x6F, 0x6D)
chonk = _chars(0x63, 0x68, 0x6F, 0x6E, 0x6B)

SPECIES: tuple[str, ...] = (
    duck,
    goose,
    blob,
    cat,
    dragon,
    octopus,
    owl,
    penguin,
    turtle,
    snail,
    ghost,
    axolotl,
    capybara,
    cactus,
    robot,
    rabbit,
    mushroom,
    chonk,
)
Species = Literal[
    "duck",
    "goose",
    "blob",
    "cat",
    "dragon",
    "octopus",
    "owl",
    "penguin",
    "turtle",
    "snail",
    "ghost",
    "axolotl",
    "capybara",
    "cactus",
    "robot",
    "rabbit",
    "mushroom",
    "chonk",
]

EYES: tuple[str, ...] = ("·", "✦", "×", "◉", "@", "°")
Eye = Literal["·", "✦", "×", "◉", "@", "°"]

HATS: tuple[str, ...] = (
    "none",
    "crown",
    "tophat",
    "propeller",
    "halo",
    "wizard",
    "beanie",
    "tinyduck",
)
Hat = Literal[
    "none",
    "crown",
    "tophat",
    "propeller",
    "halo",
    "wizard",
    "beanie",
    "tinyduck",
]

STAT_NAMES: tuple[str, ...] = ("DEBUGGING", "PATIENCE", "CHAOS", "WISDOM", "SNARK")
StatName = Literal["DEBUGGING", "PATIENCE", "CHAOS", "WISDOM", "SNARK"]


@dataclass
class CompanionBones:
    rarity: str
    species: str
    eye: str
    hat: str
    shiny: bool
    stats: dict[str, int]


@dataclass
class Companion:
    rarity: str
    species: str
    eye: str
    hat: str
    shiny: bool
    stats: dict[str, int]
    name: str
    personality: str
    hatched_at: int


@dataclass
class StoredCompanion:
    name: str
    personality: str
    hatched_at: int


RARITY_WEIGHTS: dict[str, int] = {
    "common": 60,
    "uncommon": 25,
    "rare": 10,
    "epic": 4,
    "legendary": 1,
}

RARITY_STARS: dict[str, str] = {
    "common": "★",
    "uncommon": "★★",
    "rare": "★★★",
    "epic": "★★★★",
    "legendary": "★★★★★",
}

RARITY_COLORS: dict[str, str] = {
    "common": "inactive",
    "uncommon": "success",
    "rare": "permission",
    "epic": "autoAccept",
    "legendary": "warning",
}
