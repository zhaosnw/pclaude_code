"""
Tests for buddy/companion.py — deterministic PRNG creature generation.
"""

from __future__ import annotations

from unittest import mock

from hare.buddy.companion import (
    Roll,
    companion_user_id,
    get_companion,
    hash_string,
    mulberry32,
    roll,
    roll_from,
    roll_rarity,
    roll_stats,
    roll_with_seed,
)
from hare.buddy.types import (
    RARITIES,
    STAT_NAMES,
    Companion,
    CompanionBones,
    StoredCompanion,
)


class TestHashString:
    def test_deterministic(self) -> None:
        assert hash_string("hello") == hash_string("hello")
        assert hash_string("world") == hash_string("world")

    def test_different_inputs_different_hash(self) -> None:
        assert hash_string("hello") != hash_string("world")

    def test_known_value(self) -> None:
        result = hash_string("test")
        assert isinstance(result, int)
        assert result >= 0

    def test_empty_string(self) -> None:
        result = hash_string("")
        assert isinstance(result, int)

    def test_unicode_string(self) -> None:
        result = hash_string("你好世界")
        assert isinstance(result, int)


class TestMulberry32:
    def test_deterministic(self) -> None:
        rng1 = mulberry32(42)
        rng2 = mulberry32(42)
        values1 = [rng1() for _ in range(10)]
        values2 = [rng2() for _ in range(10)]
        assert values1 == values2

    def test_different_seeds(self) -> None:
        rng1 = mulberry32(1)
        rng2 = mulberry32(2)
        values1 = [rng1() for _ in range(10)]
        values2 = [rng2() for _ in range(10)]
        assert values1 != values2

    def test_range(self) -> None:
        rng = mulberry32(1234)
        for _ in range(100):
            v = rng()
            assert 0.0 <= v < 1.0


class TestRollRarity:
    def test_returns_valid_rarity(self) -> None:
        for seed in range(20):
            rng = mulberry32(seed)
            rarity = roll_rarity(rng)
            assert rarity in RARITIES


class TestRollStats:
    def test_returns_all_stat_names(self) -> None:
        rng = mulberry32(1)
        stats = roll_stats(rng, "common")
        for name in STAT_NAMES:
            assert name in stats

    def test_stats_in_range(self) -> None:
        for rarity in RARITIES:
            rng = mulberry32(hash_string(rarity))
            stats = roll_stats(rng, rarity)
            for name, val in stats.items():
                assert 1 <= val <= 100, f"Stat {name}={val} out of range for {rarity}"


class TestRollFrom:
    def test_returns_valid_roll(self) -> None:
        rng = mulberry32(42)
        result = roll_from(rng)
        assert isinstance(result, Roll)
        assert isinstance(result.bones, CompanionBones)
        assert result.bones.rarity in RARITIES
        assert isinstance(result.inspiration_seed, int)

    def test_deterministic(self) -> None:
        rng1 = mulberry32(42)
        r1 = roll_from(rng1)
        rng2 = mulberry32(42)
        r2 = roll_from(rng2)
        assert r1.bones.rarity == r2.bones.rarity
        assert r1.bones.species == r2.bones.species
        assert r1.bones.shiny == r2.bones.shiny
        assert r1.inspiration_seed == r2.inspiration_seed

    def test_common_rarity_has_no_hat(self) -> None:
        rng = mulberry32(42)
        result = roll_from(rng)
        if result.bones.rarity == "common":
            assert result.bones.hat == "none"
        if result.bones.rarity != "common":
            assert result.bones.hat is not None


class TestRoll:
    def test_caching(self) -> None:
        r1 = roll("test-user-1")
        r2 = roll("test-user-1")
        assert r1.bones.rarity == r2.bones.rarity
        assert r1.inspiration_seed == r2.inspiration_seed

    def test_different_users_different_rolls(self) -> None:
        r1 = roll("user-a")
        r2 = roll("user-b")
        assert (r1.bones.rarity, r1.bones.species) != (
            r2.bones.rarity,
            r2.bones.species,
        )

    def test_roll_with_seed(self) -> None:
        result = roll_with_seed("my-seed")
        assert isinstance(result, Roll)
        assert isinstance(result.bones, CompanionBones)

    def test_roll_with_seed_deterministic(self) -> None:
        r1 = roll_with_seed("abc123")
        r2 = roll_with_seed("abc123")
        assert r1.bones.rarity == r2.bones.rarity
        assert r1.bones.species == r2.bones.species


class TestCompanionUserId:
    def test_returns_string(self) -> None:
        with mock.patch("hare.buddy.companion.get_global_config") as mock_cfg:
            mock_cfg.return_value = mock.Mock(oauth_account=None, user_id="test-user")
            uid = companion_user_id()
            assert isinstance(uid, str)

    def test_anon_fallback(self) -> None:
        with mock.patch("hare.buddy.companion.get_global_config") as mock_cfg:
            mock_cfg.return_value = mock.Mock(oauth_account=None, user_id=None)
            assert companion_user_id() == "anon"

    def test_from_oauth_dict(self) -> None:
        with mock.patch("hare.buddy.companion.get_global_config") as mock_cfg:
            mock_cfg.return_value = mock.Mock(
                oauth_account={"accountUuid": "abc-123"}, user_id=None
            )
            assert companion_user_id() == "abc-123"

    def test_from_oauth_object(self) -> None:
        oa = mock.Mock()
        oa.account_uuid = "uuid-from-attr"
        with mock.patch("hare.buddy.companion.get_global_config") as mock_cfg:
            mock_cfg.return_value = mock.Mock(oauth_account=oa, user_id=None)
            assert companion_user_id() == "uuid-from-attr"


class TestGetCompanion:
    def test_no_stored_returns_none(self) -> None:
        with mock.patch("hare.buddy.companion.get_global_config") as mock_cfg:
            mock_cfg.return_value = mock.Mock(
                companion=None, oauth_account=None, user_id=None
            )
            result = get_companion()
            assert result is None

    def test_stored_dict_creates_companion(self) -> None:
        with mock.patch("hare.buddy.companion.get_global_config") as mock_cfg:
            mock_cfg.return_value = mock.Mock(
                companion={
                    "name": "Buddy",
                    "personality": "friendly",
                    "hatchedAt": 1700,
                },
                oauth_account=None,
                user_id="user1",
            )
            result = get_companion()
            assert result is not None
            assert result.name == "Buddy"
            assert result.personality == "friendly"
            assert result.hatched_at == 1700

    def test_stored_companion_object(self) -> None:
        stored = StoredCompanion(name="Bones", personality="scary", hatched_at=100)
        with mock.patch("hare.buddy.companion.get_global_config") as mock_cfg:
            mock_cfg.return_value = mock.Mock(
                companion=stored, oauth_account=None, user_id="user1"
            )
            result = get_companion()
            assert result is not None
            assert result.name == "Bones"
            assert result.personality == "scary"
