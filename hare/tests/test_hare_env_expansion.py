"""Tests for hare.services.mcp.env_expansion — env var expansion with defaults."""

from __future__ import annotations

import os
import pytest
from hare.services.mcp.env_expansion import expand_env_vars_in_string, ExpandEnvResult


def test_expand_existing_env_var() -> None:
    os.environ["TEST_VAR_EXISTS"] = "hello"
    result = expand_env_vars_in_string("prefix_${TEST_VAR_EXISTS}_suffix")
    assert result.expanded == "prefix_hello_suffix"
    assert result.missing_vars == []


def test_expand_missing_var_no_default() -> None:
    result = expand_env_vars_in_string("${NONEXISTENT_VAR_XYZ}")
    assert result.expanded == "${NONEXISTENT_VAR_XYZ}"
    assert result.missing_vars == ["NONEXISTENT_VAR_XYZ"]


def test_expand_missing_var_with_default() -> None:
    result = expand_env_vars_in_string("${MISSING_VAR_ABC:-default_value}")
    assert result.expanded == "default_value"
    assert result.missing_vars == []


def test_expand_existing_var_with_default() -> None:
    os.environ["HAS_VALUE"] = "real_value"
    result = expand_env_vars_in_string("${HAS_VALUE:-fallback}")
    assert result.expanded == "real_value"
    assert result.missing_vars == []


def test_expand_no_vars() -> None:
    result = expand_env_vars_in_string("plain text with no variables")
    assert result.expanded == "plain text with no variables"
    assert result.missing_vars == []


def test_expand_multiple_vars() -> None:
    os.environ["A"] = "1"
    result = expand_env_vars_in_string("${A}_${B:-2}_${C}")
    assert result.expanded == "1_2_${C}"
    assert result.missing_vars == ["C"]


def test_expand_empty_string() -> None:
    result = expand_env_vars_in_string("")
    assert result.expanded == ""
    assert result.missing_vars == []


def test_expand_partial_default_syntax() -> None:
    result = expand_env_vars_in_string("${VAR:-}")
    assert result.expanded == ""
    assert result.missing_vars == []
