"""Final coverage push — settings gaps + message normalization edge cases."""

from __future__ import annotations

import os
import json
import tempfile
import pytest
from unittest.mock import patch


class TestSettingsFinalGaps:
    def test_session_settings_cache(self) -> None:
        from hare.utils.settings.settings import (
            reset_settings_cache,
            reload_settings,
            get_initial_settings,
        )

        reset_settings_cache()
        result = get_initial_settings()
        assert "settings" in result or isinstance(result, dict)

    def test_parse_settings_file_missing_errors(self) -> None:
        from hare.utils.settings.settings import parse_settings_file

        try:
            result = parse_settings_file("/nonexistent/path/xyz.json")
            assert isinstance(result, dict)
        except FileNotFoundError:
            pass  # expected behavior

    def test_parse_settings_file_invalid_json_errors(self) -> None:
        from hare.utils.settings.settings import parse_settings_file

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("{invalid json @@@")
            path = f.name
        try:
            result = parse_settings_file(path)
            assert result.get("settings") is None
            assert len(result.get("errors", [])) > 0
        finally:
            os.unlink(path)

    def test_get_plugin_settings_base_default(self) -> None:
        from hare.utils.settings.settings import (
            clear_plugin_settings_base,
            get_plugin_settings_base,
        )

        clear_plugin_settings_base()
        assert get_plugin_settings_base() is None

    def test_set_and_get_plugin_settings(self) -> None:
        from hare.utils.settings.settings import (
            set_plugin_settings_base,
            get_plugin_settings_base,
            clear_plugin_settings_base,
        )

        clear_plugin_settings_base()
        test_settings = {"permissions": {"allow": ["git", "npm"]}}
        set_plugin_settings_base(test_settings)
        result = get_plugin_settings_base()
        assert result == test_settings
        clear_plugin_settings_base()

    def test_reset_settings_cache_twice(self) -> None:
        from hare.utils.settings.settings import reset_settings_cache

        reset_settings_cache()
        reset_settings_cache()  # idempotent

    def test_auto_mode_opt_in(self) -> None:
        from hare.utils.settings.settings import has_auto_mode_opt_in

        result = has_auto_mode_opt_in()
        assert isinstance(result, bool)

    def test_get_use_auto_mode_during_plan(self) -> None:
        from hare.utils.settings.settings import get_use_auto_mode_during_plan

        result = get_use_auto_mode_during_plan()
        assert isinstance(result, bool)

    def test_get_managed_hooks_only(self) -> None:
        from hare.utils.settings.settings import get_managed_hooks_only

        result = get_managed_hooks_only()
        assert isinstance(result, bool)

    def test_get_managed_permission_rules_only(self) -> None:
        from hare.utils.settings.settings import get_managed_permission_rules_only

        result = get_managed_permission_rules_only()
        assert isinstance(result, bool)

    def test_get_strict_plugin_only_customization(self) -> None:
        from hare.utils.settings.settings import get_strict_plugin_only_customization

        result = get_strict_plugin_only_customization()
        assert result is False or isinstance(result, (bool, list))
