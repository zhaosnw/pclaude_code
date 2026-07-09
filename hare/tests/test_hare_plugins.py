"""
Tests for hare.plugins.loader — plugin loading.

Port of: src/plugins/pluginLoader.ts behavior verification.
"""

from __future__ import annotations

import json

import pytest

from hare.plugins.loader import PluginDefinition, load_plugin


# ---------------------------------------------------------------------------
# PluginDefinition
# ---------------------------------------------------------------------------


def test_plugin_definition_defaults() -> None:
    pd = PluginDefinition(name="test")
    assert pd.name == "test"
    assert pd.version == "0.0.0"
    assert pd.description == ""
    assert pd.tools == []
    assert pd.prompts == []
    assert pd.resources == []
    assert pd.entry_point is None


def test_plugin_definition_custom() -> None:
    pd = PluginDefinition(
        name="custom-plugin",
        version="2.0.0",
        description="A custom plugin",
        tools=[{"name": "tool1"}],
        prompts=[{"name": "prompt1"}],
        resources=[{"uri": "file:///res"}],
        entry_point="plugin.py",
    )
    assert pd.name == "custom-plugin"
    assert pd.version == "2.0.0"
    assert len(pd.tools) == 1
    assert len(pd.prompts) == 1
    assert len(pd.resources) == 1
    assert pd.entry_point == "plugin.py"


# ---------------------------------------------------------------------------
# load_plugin
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_load_plugin_nonexistent_dir(tmp_path) -> None:
    result = load_plugin(str(tmp_path / "nonexistent"))
    assert result is None


@pytest.mark.integration
def test_load_plugin_no_manifest(tmp_path) -> None:
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    result = load_plugin(str(plugin_dir))
    assert result is None


@pytest.mark.integration
def test_load_plugin_valid_manifest(tmp_path) -> None:
    plugin_dir = tmp_path / "valid_plugin"
    plugin_dir.mkdir()
    manifest = {
        "name": "my-plugin",
        "version": "1.0.0",
        "description": "Test plugin",
        "tools": [{"name": "my_tool", "description": "A tool"}],
        "prompts": [],
        "resources": [],
        "entryPoint": "main.py",
    }
    (plugin_dir / "manifest.json").write_text(json.dumps(manifest))

    result = load_plugin(str(plugin_dir))
    assert result is not None
    assert result.name == "my-plugin"
    assert result.version == "1.0.0"
    assert result.description == "Test plugin"
    assert len(result.tools) == 1
    assert result.entry_point == "main.py"


@pytest.mark.integration
def test_load_plugin_minimal_manifest(tmp_path) -> None:
    """Manifest with only name falls back to defaults."""
    plugin_dir = tmp_path / "min_plugin"
    plugin_dir.mkdir()
    (plugin_dir / "manifest.json").write_text('{"name": "minimal"}')

    result = load_plugin(str(plugin_dir))
    assert result is not None
    assert result.name == "minimal"
    assert result.version == "0.0.0"
    assert result.tools == []
    assert result.prompts == []


@pytest.mark.integration
def test_load_plugin_manifest_missing_name(tmp_path) -> None:
    """Missing name falls back to directory name."""
    plugin_dir = tmp_path / "fallback_name"
    plugin_dir.mkdir()
    (plugin_dir / "manifest.json").write_text('{"version": "1.0"}')

    result = load_plugin(str(plugin_dir))
    assert result is not None
    assert result.name == "fallback_name"


@pytest.mark.integration
def test_load_plugin_invalid_json(tmp_path) -> None:
    plugin_dir = tmp_path / "bad_json"
    plugin_dir.mkdir()
    (plugin_dir / "manifest.json").write_text("not json {{{")

    result = load_plugin(str(plugin_dir))
    assert result is None


@pytest.mark.integration
def test_load_plugin_unreadable_manifest(tmp_path) -> None:
    # Directory without read permission on manifest → OSError
    plugin_dir = tmp_path / "unreadable"
    plugin_dir.mkdir()
    manifest = plugin_dir / "manifest.json"
    manifest.write_text("{}")
    manifest.chmod(0o000)
    try:
        result = load_plugin(str(plugin_dir))
        assert result is None
    finally:
        manifest.chmod(0o644)
