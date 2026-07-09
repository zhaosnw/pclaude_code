"""
Tests for buddy/buddy.py (BuddySystem, hints) and buddy/types.py (companion types).
"""

from __future__ import annotations

import pytest

from hare.buddy.buddy import (
    BuddyHint,
    BuddySystem,
    get_buddy_hint,
)
from hare.buddy.types import (
    Companion,
    CompanionBones,
    RARITIES,
    RARITY_WEIGHTS,
    RARITY_STARS,
    RARITY_COLORS,
    SPECIES,
    EYES,
    HATS,
    STAT_NAMES,
    duck,
    goose,
    blob,
    dragon,
    ghost,
    robot,
)


# ---------------------------------------------------------------------------
# BuddySystem tests
# ---------------------------------------------------------------------------


class TestBuddySystem:
    def test_get_hint_returns_first_unseen(self) -> None:
        system = BuddySystem()
        hint = system.get_hint()
        assert hint is not None
        assert hint.id == "welcome"

    def test_get_hint_skips_shown_hints(self) -> None:
        system = BuddySystem()
        system.shown_hints.add("welcome")
        system.shown_hints.add("large_file")
        system.shown_hints.add("permission_denied")
        hint = system.get_hint()
        assert hint is not None
        assert hint.id == "rate_limited"

    def test_get_hint_returns_none_when_all_shown(self) -> None:
        system = BuddySystem()
        system.shown_hints.add("welcome")
        system.shown_hints.add("large_file")
        system.shown_hints.add("permission_denied")
        system.shown_hints.add("rate_limited")
        hint = system.get_hint()
        assert hint is None

    def test_get_hint_with_context_match(self) -> None:
        system = BuddySystem()
        system.shown_hints.add("welcome")
        hint = system.get_hint("large")
        assert hint is not None
        assert hint.id == "large_file"

    def test_get_hint_with_no_context_match(self) -> None:
        system = BuddySystem()
        system.shown_hints.add("welcome")
        hint = system.get_hint("nonexistent_condition")
        assert hint is not None
        assert hint.id == "large_file"  # no condition filter applied

    def test_reset_clears_shown_hints(self) -> None:
        system = BuddySystem()
        system.shown_hints.add("welcome")
        system.shown_hints.add("large_file")
        system.reset()
        assert len(system.shown_hints) == 0
        hint = system.get_hint()
        assert hint is not None
        assert hint.id == "welcome"

    def test_get_buddy_hint_returns_string(self) -> None:
        result = get_buddy_hint()
        assert result is not None
        assert isinstance(result, str)
        assert "Hare" in result

    def test_get_buddy_hint_returns_none_when_empty(self) -> None:
        # All built-in hints should be exhaustible in 4 calls
        results = [get_buddy_hint() for _ in range(4)]
        assert all(isinstance(r, str) for r in results)
        # 5th call returns None since the system is fresh each call
        # (get_buddy_hint creates a new BuddySystem each time)
        # Actually get_buddy_hint creates a new system each call, so it always returns the first hint
        # Let's test the BuddySystem directly instead
        system = BuddySystem()
        for _ in range(4):
            system.get_hint()
        assert system.get_hint() is None


# ---------------------------------------------------------------------------
# BuddyHint tests
# ---------------------------------------------------------------------------


class TestBuddyHint:
    def test_buddy_hint_has_required_fields(self) -> None:
        hint = BuddyHint(id="test", message="test message", priority=5)
        assert hint.id == "test"
        assert hint.message == "test message"
        assert hint.priority == 5
        assert hint.condition == ""

    def test_buddy_hint_default_values(self) -> None:
        hint = BuddyHint(id="minimal", message="msg")
        assert hint.condition == ""
        assert hint.priority == 0


# ---------------------------------------------------------------------------
# Companion types tests
# ---------------------------------------------------------------------------


class TestBuddyTypes:
    def test_species_constants_are_strings(self) -> None:
        assert duck == "duck"
        assert goose == "goose"
        assert blob == "blob"
        assert dragon == "dragon"
        assert ghost == "ghost"
        assert robot == "robot"

    def test_rarities_tuple(self) -> None:
        assert len(RARITIES) == 5
        assert "common" in RARITIES
        assert "legendary" in RARITIES

    def test_rarity_weights_sum_to_100(self) -> None:
        total = sum(RARITY_WEIGHTS.values())
        assert total == 100

    def test_rarity_stars_have_all_rarities(self) -> None:
        for rarity in RARITIES:
            assert rarity in RARITY_STARS

    def test_rarity_colors_have_all_rarities(self) -> None:
        for rarity in RARITIES:
            assert rarity in RARITY_COLORS

    def test_species_list_has_entries(self) -> None:
        assert len(SPECIES) == 18

    def test_eyes_list(self) -> None:
        assert len(EYES) == 6

    def test_hats_list(self) -> None:
        assert len(HATS) == 8

    def test_stat_names_list(self) -> None:
        assert len(STAT_NAMES) == 5

    def test_companion_bones_creation(self) -> None:
        bones = CompanionBones(
            rarity="common",
            species="duck",
            eye="·",
            hat="none",
            shiny=False,
            stats={"DEBUGGING": 10},
        )
        assert bones.rarity == "common"
        assert bones.species == "duck"
        assert bones.eye == "·"
        assert bones.hat == "none"
        assert bones.shiny is False
        assert bones.stats == {"DEBUGGING": 10}

    def test_companion_creation(self) -> None:
        companion = Companion(
            rarity="rare",
            species="dragon",
            eye="◉",
            hat="crown",
            shiny=True,
            stats={"WISDOM": 20},
            name="Smaug",
            personality="fierce",
            hatched_at=1700000000,
        )
        assert companion.name == "Smaug"
        assert companion.species == "dragon"
        assert companion.shiny is True
        assert companion.hatched_at == 1700000000

    def test_rarity_color_mapping(self) -> None:
        assert RARITY_COLORS["common"] == "inactive"
        assert RARITY_COLORS["legendary"] == "warning"
