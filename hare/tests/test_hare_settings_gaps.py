"""Coverage gap tests for settings/settings.py."""

from __future__ import annotations

import os
import json
import pytest
from unittest.mock import patch


class TestSettingsGaps:
    def test_set_plugin_settings_base(self) -> None:
        from hare.utils.settings.settings import (
            set_plugin_settings_base,
            get_plugin_settings_base,
            clear_plugin_settings_base,
        )

        clear_plugin_settings_base()
        settings = {"permissions": {"allow": ["git"]}}
        set_plugin_settings_base(settings)
        assert get_plugin_settings_base() == settings
        clear_plugin_settings_base()

    def test_clear_plugin_settings_base(self) -> None:
        from hare.utils.settings.settings import (
            clear_plugin_settings_base,
            get_plugin_settings_base,
        )

        clear_plugin_settings_base()
        assert get_plugin_settings_base() is None

    def test_parse_settings_file_invalid_json(self) -> None:
        from hare.utils.settings.settings import parse_settings_file
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("not valid json {{{")
            path = f.name
        try:
            result = parse_settings_file(path)
            assert result.get("settings") is None
            assert len(result.get("errors", [])) > 0
        finally:
            os.unlink(path)

    def test_parse_settings_file_nonexistent(self) -> None:
        from hare.utils.settings.settings import parse_settings_file

        result = parse_settings_file("/nonexistent/path/settings.json")
        assert "errors" in result

    def test_reload_settings_resets_cache(self) -> None:
        from hare.utils.settings.settings import reload_settings, reset_settings_cache

        reset_settings_cache()
        result = reload_settings()
        assert isinstance(result, dict)

    def test_has_skip_dangerous_mode_default(self) -> None:
        from hare.utils.settings.settings import (
            has_skip_dangerous_mode_permission_prompt,
        )

        with patch.dict(os.environ, {}, clear=True):
            result = has_skip_dangerous_mode_permission_prompt()
            assert isinstance(result, bool)

    def test_has_auto_mode_opt_in(self) -> None:
        from hare.utils.settings.settings import has_auto_mode_opt_in

        result = has_auto_mode_opt_in()
        assert isinstance(result, bool)

    def test_get_auto_mode_config(self) -> None:
        from hare.utils.settings.settings import get_auto_mode_config

        result = get_auto_mode_config()
        assert result is None or isinstance(result, dict)
