import json

import pytest

from hare.testing.fake_model import fixture_call_model, load_fixture


async def _aiter(value):
    # call_model 可能返回 async-gen 或 coroutine-of-async-gen,统一展开
    if hasattr(value, "__aiter__"):
        async for x in value:
            yield x
        return
    inner = await value
    async for x in inner:
        yield x


@pytest.mark.asyncio
async def test_fixture_call_model_yields_assistant_dicts_in_order(tmp_path):
    fixture_path = tmp_path / "fx.json"
    fixture_path.write_text(
        json.dumps(
            {
                "kind": "scripted",
                "responses": [
                    {
                        "stop_reason": "tool_use",
                        "content": [
                            {"type": "tool_use", "id": "t1", "name": "Read", "input": {}}
                        ],
                        "usage": {"input_tokens": 1, "output_tokens": 1},
                    },
                    {
                        "stop_reason": "end_turn",
                        "content": [{"type": "text", "text": "done"}],
                        "usage": {"input_tokens": 1, "output_tokens": 1},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    fixture = load_fixture(fixture_path)
    call_model = fixture_call_model(fixture)

    # 第一次调用 -> 第一条 response(assistant dict)
    msgs = [m async for m in _aiter(call_model({"messages": []}))]
    assert msgs[-1]["type"] == "assistant"
    assert msgs[-1]["stop_reason"] == "tool_use"
    # 第二次调用 -> 第二条 response
    msgs = [m async for m in _aiter(call_model({"messages": []}))]
    assert msgs[-1]["stop_reason"] == "end_turn"
    assert msgs[-1]["content"][0]["text"] == "done"


@pytest.mark.asyncio
async def test_fixture_exhaustion_raises(tmp_path):
    fixture_path = tmp_path / "fx.json"
    fixture_path.write_text(
        json.dumps(
            {
                "kind": "scripted",
                "responses": [
                    {
                        "stop_reason": "end_turn",
                        "content": [{"type": "text", "text": "only"}],
                        "usage": {"input_tokens": 1, "output_tokens": 1},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    call_model = fixture_call_model(load_fixture(fixture_path))
    _ = [m async for m in _aiter(call_model({}))]
    with pytest.raises(AssertionError, match="fixture exhausted"):
        _ = [m async for m in _aiter(call_model({}))]


def test_load_fixture_rejects_bad_kind(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text(json.dumps({"kind": "nope", "responses": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="kind"):
        load_fixture(p)
