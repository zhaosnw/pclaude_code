import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

from mock_anthropic_server import make_server, stream_response  # noqa: E402


# Statuses the official Anthropic SDK's shouldRetry() treats as transient and
# retries (with backoff): 408, 409, 429, and any >=500. A mock-server error
# that means "this fixture doesn't have a matching response" is a
# fixture-author bug, not a transient failure, and must NOT be one of these —
# otherwise the SDK burns through its retry budget before the caller ever
# sees the actual error message.
_SDK_RETRIED_STATUSES = {408, 409, 429}


def _is_sdk_retried(status: int) -> bool:
    return status in _SDK_RETRIED_STATUSES or status >= 500


def test_stream_response_shape_text():
    sse = stream_response(
        {
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": "abc"}],
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
    ).decode()
    assert "event: message_start" in sse
    assert "event: content_block_start" in sse
    assert '"text":"abc"' in sse.replace(" ", "")
    assert '"stop_reason":"end_turn"' in sse.replace(" ", "")
    assert "event: message_stop" in sse


def test_stream_response_shape_tool_use():
    sse = stream_response(
        {
            "stop_reason": "tool_use",
            "content": [
                {"type": "tool_use", "id": "t1", "name": "Read", "input": {"x": 1}}
            ],
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
    ).decode()
    assert '"type":"tool_use"' in sse.replace(" ", "")
    assert '"input_json_delta"' in sse.replace(" ", "")


def test_server_serves_next_response_per_post(tmp_path):
    fx = tmp_path / "fx.json"
    fx.write_text(
        json.dumps(
            {
                "kind": "scripted",
                "responses": [
                    {
                        "stop_reason": "end_turn",
                        "content": [{"type": "text", "text": "first"}],
                        "usage": {"input_tokens": 1, "output_tokens": 1},
                    },
                    {
                        "stop_reason": "end_turn",
                        "content": [{"type": "text", "text": "second"}],
                        "usage": {"input_tokens": 1, "output_tokens": 1},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    server = make_server(fx, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    # Bypass any ambient HTTP proxy — 127.0.0.1 must connect directly.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        bodies = []
        for _ in range(2):
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/v1/messages",
                data=b'{"stream": true}',
                headers={"content-type": "application/json"},
            )
            bodies.append(opener.open(req, timeout=5).read().decode())
    finally:
        server.shutdown()
    assert '"text":"first"' in bodies[0].replace(" ", "")
    assert '"text":"second"' in bodies[1].replace(" ", "")


def test_no_matching_content_matched_response_is_not_sdk_retried(tmp_path):
    fx = tmp_path / "fx.json"
    fx.write_text(
        json.dumps(
            {
                "kind": "content-matched",
                "responses": [
                    {
                        "match": {"user_contains": "never present"},
                        "content": [{"type": "text", "text": "unreachable"}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    server = make_server(fx, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/messages",
            data=json.dumps({"messages": [{"role": "user", "content": "hi"}]}).encode(),
            headers={"content-type": "application/json"},
        )
        try:
            opener.open(req, timeout=5)
            raise AssertionError("expected an HTTPError for the unmatched request")
        except urllib.error.HTTPError as exc:
            status = exc.code
    finally:
        server.shutdown()
    assert not _is_sdk_retried(status), (
        f"status {status} is retried by the Anthropic SDK's shouldRetry() — "
        "a fixture-author 'no match' error must fail fast, not trigger a "
        "retry-then-timeout"
    )


def test_exhausted_scripted_fixture_is_not_sdk_retried(tmp_path):
    fx = tmp_path / "fx.json"
    fx.write_text(
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
    server = make_server(fx, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/messages",
            data=b'{"stream": true}',
            headers={"content-type": "application/json"},
        )
        opener.open(req, timeout=5).read()  # first call consumes the only response
        try:
            opener.open(req, timeout=5)
            raise AssertionError("expected an HTTPError once the fixture is exhausted")
        except urllib.error.HTTPError as exc:
            status = exc.code
    finally:
        server.shutdown()
    assert not _is_sdk_retried(status), (
        f"status {status} is retried by the Anthropic SDK's shouldRetry() — "
        "fixture exhaustion must fail fast, not trigger a retry-then-timeout"
    )
