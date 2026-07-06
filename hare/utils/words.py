"""
Word slug generation.

Port of: src/utils/words.ts
"""

from __future__ import annotations

import random

_ADJECTIVES = [
    "bright",
    "calm",
    "dark",
    "eager",
    "fast",
    "green",
    "happy",
    "idle",
    "keen",
    "late",
    "mild",
    "neat",
    "open",
    "plain",
    "quick",
    "rare",
    "safe",
    "tall",
    "warm",
    "young",
]

_NOUNS = [
    "arch",
    "beam",
    "core",
    "dawn",
    "edge",
    "flux",
    "gate",
    "haze",
    "iris",
    "jade",
    "knot",
    "lens",
    "mesh",
    "node",
    "opus",
    "peak",
    "reef",
    "slab",
    "tide",
    "vale",
]


def generate_word_slug() -> str:
    adj = random.choice(_ADJECTIVES)
    noun = random.choice(_NOUNS)
    num = random.randint(10, 99)
    return f"{adj}-{noun}-{num}"
