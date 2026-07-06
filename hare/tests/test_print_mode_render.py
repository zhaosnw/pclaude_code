"""Regression tests for the print-mode renderer in hare/main.py.

Pins the bug found by the TS differential: in print_result_only mode hare
emitted a spurious LEADING newline before the result text (Claude Code does
not), because the "newline after streaming output" print() fired even though
nothing had been streamed.
"""

from hare.main import _render_engine_event


def _assistant_text(text: str) -> dict:
    class _Msg:
        def __init__(self, content):
            self.content = content

    class _Wrap:
        def __init__(self, content):
            self.message = _Msg(content)

    return {"type": "assistant", "message": _Wrap(text)}


def test_result_only_has_no_leading_newline(capsys):
    # The print path (-p) renders only the result event with print_result_only=True.
    _render_engine_event(
        {"type": "result", "result": "Hello from fixture."},
        print_result_only=True,
    )
    out = capsys.readouterr().out
    assert out == "Hello from fixture.\n", repr(out)


def test_result_only_empty_result_still_prints_newline(capsys):
    # Claude Code prints '\n' for an empty result; hare must match (not '').
    _render_engine_event({"type": "result", "result": ""}, print_result_only=True)
    out = capsys.readouterr().out
    assert out == "\n", repr(out)


def test_streaming_mode_still_terminates_with_newline(capsys):
    # Interactive/streaming path (print_result_only=False): assistant text is
    # streamed with end="" and the result event must still terminate the line.
    _render_engine_event(_assistant_text("Hello from fixture."), print_result_only=False)
    _render_engine_event({"type": "result"}, print_result_only=False)
    out = capsys.readouterr().out
    assert out == "Hello from fixture.\n", repr(out)
