"""Request-side alignment: what hare SENDS to the model (not what it prints).

Fixture-replay output tests can't see the request — the model output is fixed
regardless of the request. These checks capture the payload hare's query loop
builds (via a capturing call_model injected through production_deps) and pin
known request-side reproduction gaps so progress/regressions are tracked.

Findings recorded in docs/alignment-findings.md.
"""

import asyncio

import pytest


def _capture_payload(prompt: str) -> dict:
    """Run hare's query loop in-process and capture the payload passed to the
    model (hare's constructed request, pre-SDK-formatting)."""
    cap: dict = {}

    def _make():
        async def call_model(payload, *a, **k):
            cap["payload"] = payload
            yield {
                "type": "assistant",
                "content": [{"type": "text", "text": "ok"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
        return call_model

    import hare.query.core as core
    import hare.query.deps as deps
    from hare.query.deps import QueryDeps

    orig = deps.production_deps

    def patched():
        d = orig()
        return QueryDeps(call_model=_make(), microcompact=d.microcompact,
                         autocompact=d.autocompact, uuid=d.uuid)

    deps.production_deps = patched
    core.production_deps = patched
    try:
        async def run():
            from hare.sdk import HareClient, HareClientOptions
            from hare.utils.cwd import get_cwd
            c = await HareClient.create(HareClientOptions(cwd=get_cwd()))
            async for _ in c.stream(prompt):
                pass
        asyncio.run(run())
    finally:
        deps.production_deps = orig
        core.production_deps = orig
    return cap.get("payload", {})


@pytest.mark.integration
def test_request_envelope_has_core_fields():
    """The SDK request hare builds from its payload carries the core Anthropic
    fields and a tools array of {name, description, input_schema} dicts — the
    same shape Claude Code sends (verified against a captured reference request)."""
    import dataclasses

    from hare.services.api.claude import build_api_request_payload

    p = _capture_payload("say hi")
    assert isinstance(p.get("messages"), list) and p["messages"], "messages missing"
    assert isinstance(p.get("tools"), list) and p["tools"], "tools missing"

    o = p.get("options", {})
    api = build_api_request_payload(
        messages=p["messages"],
        system_prompt=p.get("system_prompt", []),
        model=o.get("model") or "claude-sonnet-4-20250514",
        tools=p["tools"],
        thinking_config=p.get("thinking_config"),
        max_tokens_override=o.get("max_output_tokens_override"),
        stream=True,
        prompt_caching=True,
    )
    d = dataclasses.asdict(api) if dataclasses.is_dataclass(api) else api.__dict__
    for key in ("model", "messages", "system", "tools", "max_tokens"):
        assert key in d, f"built request missing {key!r}"
    tool0 = d["tools"][0]
    assert {"name", "description", "input_schema"} <= set(tool0), tool0


@pytest.mark.integration
def test_default_system_prompt_is_assembled_and_sent():
    """hare must send the default Claude Code system prompt (was a gap: the engine
    only used custom/append). It assembles get_system_prompt() and splits on the
    cache boundary into multiple blocks; a plain invocation sends a populated
    system prompt whose static prefix is the identity section."""
    p = _capture_payload("say hi")
    sp = p.get("system_prompt")
    assert isinstance(sp, list) and len(sp) >= 2, (
        f"expected a multi-block default system prompt, got {sp!r}"
    )
    joined = "\n\n".join(sp)
    assert "interactive agent that helps users with software engineering" in joined
    # the cache-boundary marker must be consumed (split), not shipped verbatim
    assert all("__SYSTEM_PROMPT_DYNAMIC_BOUNDARY__" not in b for b in sp)
