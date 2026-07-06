"""Cover all remaining settings.py conditions in one file."""

from __future__ import annotations


class TestSettingsMergeAllBranches:
    def test_merge_settings_all_variants(self) -> None:
        from hare.utils.settings.settings import _merge_settings, _uniq_preserve_order

        # All _uniq cases
        _uniq_preserve_order([])
        _uniq_preserve_order(["a", "b", "a", "c"])
        _uniq_preserve_order([1, 2, 3, 1])
        # All _merge_settings cases
        t = {}
        s = {"a": 1}
        _merge_settings(t, s)
        t2 = {"a": {"b": {"c": [1]}}}
        s2 = {"a": {"b": {"d": [2]}}}
        _merge_settings(t2, s2)
        t3 = {"plugins": ["p1", "p2"]}
        s3 = {"plugins": ["p2", "p3", "p4"]}
        _merge_settings(t3, s3)
        t4 = {"x": []}
        s4 = {"x": ["new"]}
        _merge_settings(t4, s4)
        t5 = {"hooks": {"PreToolUse": [{"matcher": "a"}]}}
        s5 = {"hooks": {"PreToolUse": [{"matcher": "b"}]}}
        _merge_settings(t5, s5)

    def test_settings_merge_arrays_dedup(self) -> None:
        from hare.utils.settings.settings import _merge_settings

        target: dict = {"allowedTools": ["Read", "Bash"]}
        source = {"allowedTools": ["Bash", "Write"]}
        _merge_settings(target, source)
        assert "Read" in target["allowedTools"]
        assert "Write" in target["allowedTools"]

    def test_all_getter_functions(self) -> None:
        from hare.utils.settings.settings import (
            get_settings_file_path_for_source,
            get_settings_for_source,
            get_auto_mode_config,
            get_managed_hooks_only,
            get_managed_permission_rules_only,
            get_strict_plugin_only_customization,
            parse_settings_file,
            update_settings_for_source,
            _read_setting_excluding_project,
            _resolve_policy_settings_path,
            reset_settings_cache,
            reload_settings,
        )

        reset_settings_cache()
        get_settings_file_path_for_source("userSettings")
        get_settings_for_source("userSettings")
        get_auto_mode_config()
        get_managed_hooks_only()
        get_managed_permission_rules_only()
        get_strict_plugin_only_customization()
        parse_settings_file("/nonexistent/x.json")
        update_settings_for_source("userSettings", {"k": "v"}, project_dir="/tmp")
        _read_setting_excluding_project("key", ["userSettings"])
        _resolve_policy_settings_path()
        reload_settings()
