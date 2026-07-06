"""Port of: src/buddy/sprites.ts — ASCII companion sprites."""

from __future__ import annotations

from .sprite_bodies import BODIES
from .types import (
    CompanionBones,
    Eye,
    Species,
    chonk,
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
)

HAT_LINES: dict[str, str] = {
    "none": "",
    "crown": "   \\^^^/    ",
    "tophat": "   [___]    ",
    "propeller": "    -+-     ",
    "halo": "   (   )    ",
    "wizard": "    /^\\     ",
    "beanie": "   (___)    ",
    "tinyduck": "    ,>      ",
}


def render_sprite(bones: CompanionBones, frame: int = 0) -> list[str]:
    frames = BODIES[bones.species]
    body = [line.replace("{E}", bones.eye) for line in frames[frame % len(frames)]]
    lines = list(body)
    if bones.hat != "none" and not lines[0].strip():
        lines[0] = HAT_LINES[bones.hat]
    if not lines[0].strip() and all(not f[0].strip() for f in frames):
        lines.pop(0)
    return lines


def sprite_frame_count(species: str) -> int:
    return len(BODIES[species])


def render_face(bones: CompanionBones) -> str:
    eye: Eye = bones.eye  # type: ignore[assignment]
    s: Species = bones.species  # type: ignore[assignment]
    if s in (duck, goose):
        return f"({eye}>"
    if s == blob:
        return f"({eye}{eye})"
    if s == cat:
        return f"={eye}ω{eye}="
    if s == dragon:
        return f"<{eye}~{eye}>"
    if s == octopus:
        return f"~({eye}{eye})~"
    if s == owl:
        return f"({eye})({eye})"
    if s == penguin:
        return f"({eye}>)"
    if s == turtle:
        return f"[{eye}_{eye}]"
    if s == snail:
        return f"{eye}(@)"
    if s == ghost:
        return f"/{eye}{eye}\\"
    if s == axolotl:
        return "}" + eye + "." + eye + "{"
    if s == capybara:
        return f"({eye}oo{eye})"
    if s == cactus:
        return f"|{eye}  {eye}|"
    if s == robot:
        return f"[{eye}{eye}]"
    if s == rabbit:
        return f"({eye}..{eye})"
    if s == mushroom:
        return f"|{eye}  {eye}|"
    if s == chonk:
        return f"({eye}.{eye})"
    return f"({eye})"
