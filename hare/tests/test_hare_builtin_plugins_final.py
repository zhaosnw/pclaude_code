"""Hit all builtin_plugins branches by registering test plugins."""

from __future__ import annotations


class TestBuiltinPluginsFull:
    def test_register_and_get(self) -> None:
        from hare.plugins.builtin_plugins import (
            register_builtin_plugin,
            get_builtin_plugins,
            get_builtin_plugin_skill_commands,
            clear_builtin_plugins,
        )

        clear_builtin_plugins()

        # Register a test plugin with skills to exercise all branches
        register_builtin_plugin(
            {
                "name": "test-plugin",
                "description": "A test builtin",
                "version": "1.0.0",
                "skills": [
                    {
                        "name": "test-skill",
                        "description": "A test skill",
                    }
                ],
                "defaultEnabled": True,
            }
        )

        # Also register one that is disabled
        register_builtin_plugin(
            {
                "name": "test-disabled",
                "description": "A disabled plugin",
                "version": "1.0.0",
                "defaultEnabled": False,
            }
        )

        # Without settings — all defaults
        result = get_builtin_plugins(None)
        assert len(result["enabled"]) >= 1
        assert len(result["disabled"]) >= 1

        # With settings override
        def get_settings():
            return {
                "enabledPlugins": {
                    "test-plugin@builtin": True,
                    "test-disabled@builtin": True,
                }
            }

        result2 = get_builtin_plugins(get_settings)
        assert len(result2["enabled"]) >= 2

        # With settings explicitly disabling
        def get_settings2():
            return {"enabledPlugins": {"test-plugin@builtin": False}}

        result3 = get_builtin_plugins(get_settings2)
        assert isinstance(result3, dict)

        # get skill commands
        cmds = get_builtin_plugin_skill_commands(
            lambda: {"enabledPlugins": {"test-plugin@builtin": True}}
        )
        assert len(cmds) >= 1

        clear_builtin_plugins()

    def test_plugin_not_available(self) -> None:
        """L50: is_available truthy AND is_available() returns True → skip."""
        from hare.plugins.builtin_plugins import (
            register_builtin_plugin,
            get_builtin_plugins,
            clear_builtin_plugins,
        )

        clear_builtin_plugins()

        # Plugin that reports itself as unavailable
        register_builtin_plugin(
            {
                "name": "unavailable-plugin",
                "description": "Should be skipped",
                "version": "1.0.0",
                "isAvailable": lambda: False,
                "defaultEnabled": True,
            }
        )

        result = get_builtin_plugins(None)
        # Plugin marked isAvailable=False should be skipped
        assert len(result["enabled"]) + len(result["disabled"]) == 0

        clear_builtin_plugins()
