"""Close remaining P0/P1 branch coverage gaps in small files."""

from __future__ import annotations


class TestCompactPromptBranches:
    def test_import(self) -> None:
        from hare.services.compact import prompt as p

        assert p is not None


class TestSettingsTypesBranches:
    def test_settings_schema(self) -> None:
        from hare.utils.settings.types import SettingsSchema

        schema = SettingsSchema()
        assert schema["type"] == "object"
        assert "mcpServers" in schema["properties"]

    def test_permission_rule(self) -> None:
        from hare.utils.settings.types import PermissionRule

        rule: PermissionRule = {"type": "allow", "tool": "Read"}
        assert rule["type"] == "allow"


class TestCostHookBranches:
    def test_register_twice(self) -> None:
        from hare.cost_hook import register_cost_summary_hook

        register_cost_summary_hook()
        register_cost_summary_hook()  # idempotent guard


class TestEnvExpansion:
    def test_expand_empty_string(self) -> None:
        from hare.services.mcp.env_expansion import expand_env_vars_in_string

        result = expand_env_vars_in_string("")
        assert result.expanded == ""
        assert result.missing_vars == []


class TestBuiltinPlugins:
    def test_get_commands(self) -> None:
        from hare.plugins.builtin_plugins import get_builtin_plugin_skill_commands

        commands = get_builtin_plugin_skill_commands()
        assert isinstance(commands, list)


class TestErrors:
    def test_error_message_exc(self) -> None:
        from hare.utils.errors import error_message

        result = error_message(ValueError("test"))
        assert isinstance(result, str)

    def test_error_message_str(self) -> None:
        from hare.utils.errors import error_message

        result = error_message("plain error")
        assert isinstance(result, str)

    def test_error_message_none(self) -> None:
        from hare.utils.errors import error_message

        result = error_message(None)
        assert isinstance(result, str)
