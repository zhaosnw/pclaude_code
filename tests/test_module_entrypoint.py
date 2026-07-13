from __future__ import annotations


def test_module_entrypoint_delegates_to_cli_entrypoint(monkeypatch) -> None:
    from hare import __main__ as module_entrypoint
    from hare.entrypoints import cli as entry_cli

    called = False

    def fake_main() -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(entry_cli, "main", fake_main)

    module_entrypoint.main()

    assert called
