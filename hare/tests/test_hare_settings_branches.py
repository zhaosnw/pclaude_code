"""Cover remaining branches in utils/settings/settings.py merge logic."""

from __future__ import annotations


class TestSettingsMergeBranches:
    """Hit the else/elif branches in _merge_settings."""

    def test_uniq_preserve_order_empty(self) -> None:
        from hare.utils.settings.settings import _uniq_preserve_order

        assert _uniq_preserve_order([]) == []

    def test_uniq_preserve_order_duplicates(self) -> None:
        from hare.utils.settings.settings import _uniq_preserve_order

        result = _uniq_preserve_order(["a", "b", "a", "c", "b"])
        assert result == ["a", "b", "c"]

    def test_uniq_with_tuples(self) -> None:
        from hare.utils.settings.settings import _uniq_preserve_order

        result = _uniq_preserve_order([("a", 1), ("b", 2), ("a", 1)])
        assert len(result) == 2

    def test_merge_simple_values(self) -> None:
        from hare.utils.settings.settings import _merge_settings

        target = {"key1": "old"}
        source = {"key1": "new", "key2": "added"}
        _merge_settings(target, source)
        assert target["key1"] == "new"
        assert target["key2"] == "added"

    def test_merge_dict_into_dict(self) -> None:
        from hare.utils.settings.settings import _merge_settings

        target = {"tools": {"allow": ["bash"]}}
        source = {"tools": {"deny": ["rm"]}}
        _merge_settings(target, source)
        assert "allow" in target["tools"]
        assert "deny" in target["tools"]

    def test_merge_list_into_list(self) -> None:
        from hare.utils.settings.settings import _merge_settings

        target = {"plugins": ["p1", "p2"]}
        source = {"plugins": ["p2", "p3"]}
        _merge_settings(target, source)
        assert "p1" in target["plugins"]
        assert "p3" in target["plugins"]

    def test_merge_list_into_empty(self) -> None:
        from hare.utils.settings.settings import _merge_settings

        target: dict = {"plugins": []}
        source = {"plugins": ["p1", "p2"]}
        _merge_settings(target, source)
        assert len(target["plugins"]) == 2

    def test_merge_with_sub_dict_and_list(self) -> None:
        from hare.utils.settings.settings import _merge_settings

        target = {"hooks": {"PreToolUse": [{"matcher": "a"}]}}
        source = {"hooks": {"PreToolUse": [{"matcher": "b"}]}}
        _merge_settings(target, source)
        assert isinstance(target["hooks"]["PreToolUse"], list)

    def test_merge_nested_deep(self) -> None:
        from hare.utils.settings.settings import _merge_settings

        target = {"a": {"b": {"c": "old"}}}
        source = {"a": {"b": {"d": "new"}}}
        _merge_settings(target, source)
        assert target["a"]["b"]["d"] == "new"
