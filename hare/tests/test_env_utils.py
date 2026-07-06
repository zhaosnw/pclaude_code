"""
Tests for hare.utils.env_utils — environment variable helpers.

Port of: src/utils/envUtils.ts behavior verification.
"""

from __future__ import annotations


import pytest

from hare.utils.env_utils import (
    get_hare_config_home_dir,
    has_node_option,
    is_bare_mode,
    is_env_defined_falsy,
    is_env_truthy,
)


# ---------------------------------------------------------------------------
# is_env_truthy
# ---------------------------------------------------------------------------


def test_is_env_truthy_none() -> None:
    assert is_env_truthy(None) is False


def test_is_env_truthy_true_values() -> None:
    for v in ("1", "true", "yes", "TRUE", "YES", "True", "Yes"):
        assert is_env_truthy(v) is True, f"'{v}' should be truthy"


def test_is_env_truthy_false_values() -> None:
    for v in ("0", "false", "no", "", "other", "maybe"):
        assert is_env_truthy(v) is False, f"'{v}' should be falsy"


# ---------------------------------------------------------------------------
# is_env_defined_falsy
# ---------------------------------------------------------------------------


def test_is_env_defined_falsy_none() -> None:
    assert is_env_defined_falsy(None) is False


def test_is_env_defined_falsy_true_values() -> None:
    for v in ("0", "false", "no", "FALSE", "NO"):
        assert is_env_defined_falsy(v) is True, f"'{v}' should be defined-falsy"


def test_is_env_defined_falsy_other_values() -> None:
    for v in ("1", "true", "yes", "", "maybe"):
        assert is_env_defined_falsy(v) is False, f"'{v}' should not be defined-falsy"


# ---------------------------------------------------------------------------
# is_bare_mode
# ---------------------------------------------------------------------------


def test_is_bare_mode_default_false(monkeypatch) -> None:
    monkeypatch.delenv("CLAUDE_CODE_SIMPLE", raising=False)
    assert is_bare_mode() is False


def test_is_bare_mode_true(monkeypatch) -> None:
    monkeypatch.setenv("CLAUDE_CODE_SIMPLE", "1")
    assert is_bare_mode() is True


def test_is_bare_mode_false_explicit(monkeypatch) -> None:
    monkeypatch.setenv("CLAUDE_CODE_SIMPLE", "0")
    assert is_bare_mode() is False


# ---------------------------------------------------------------------------
# get_hare_config_home_dir
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_get_hare_config_home_dir_default(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("HARE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    result = get_hare_config_home_dir()
    assert result == str(tmp_path / ".hare")


def test_get_hare_config_home_dir_custom(monkeypatch) -> None:
    monkeypatch.setenv("HARE_CONFIG_DIR", "/custom/config/path")
    result = get_hare_config_home_dir()
    assert result == "/custom/config/path"


# ---------------------------------------------------------------------------
# has_node_option
# ---------------------------------------------------------------------------


def test_has_node_option_in_env(monkeypatch) -> None:
    monkeypatch.setenv("NODE_OPTIONS", "--expose-gc --some-flag")
    assert has_node_option("--expose-gc") is True
    assert has_node_option("--nonexistent") is False


def test_has_node_option_not_in_env(monkeypatch) -> None:
    monkeypatch.delenv("NODE_OPTIONS", raising=False)
    assert has_node_option("--anything") is False
