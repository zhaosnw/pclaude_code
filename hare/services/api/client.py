"""
Anthropic API client with streaming support.

Port of: src/services/api/hare.ts

Handles:
- Message construction (user/assistant → API format)
- Streaming and non-streaming requests
- Tool schema construction
- Usage tracking
- Retry logic
- Prompt caching
"""

from __future__ import annotations

import os
import json
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Optional, Sequence, Callable
from urllib.parse import urlparse
from uuid import uuid4

VERSION = "2.1.88"  # inline to avoid namespace-package import issue
from hare.bootstrap.state import get_session_id
from hare.services.api.logging import NonNullableUsage
from hare.services.api.errors import OverloadedError, RateLimitError
from hare.app_types.message import APIMessage, AssistantMessage, StreamEvent
from hare.utils.model import normalize_model_string_for_api
from hare.services.analytics.growthbook import get_feature_value_cached_may_be_stale
from hare.utils.messages import create_assistant_api_error_message
from hare.bootstrap.state import get_is_non_interactive_session
from hare.utils.env_utils import is_env_truthy

MAX_NON_STREAMING_TOKENS = 128
MAX_OUTPUT_TOKENS_DEFAULT = 16384
MAX_OUTPUT_TOKENS_THINKING = 32768
MAX_529_RETRIES = 3
MAX_RETRIES = 3
BASE_RETRY_DELAY_SECONDS = 1.0
API_ERROR_MESSAGE_PREFIX = "API Error"


@dataclass
class APIRequestParams:
    model: str = ""
    messages: list[dict[str, Any]] = field(default_factory=list)
    system: str | list[dict[str, Any]] = ""
    max_tokens: int = MAX_OUTPUT_TOKENS_DEFAULT
    tools: list[dict[str, Any]] = field(default_factory=list)
    temperature: float = 1.0
    stream: bool = True
    thinking: Optional[dict[str, Any]] = None
    metadata: Optional[dict[str, Any]] = None


class FallbackTriggeredError(Exception):
    def __init__(self, original_model: str, fallback_model: str) -> None:
        super().__init__(
            f"Model fallback triggered: {original_model} -> {fallback_model}"
        )
        self.original_model = original_model
        self.fallback_model = fallback_model


def get_max_output_tokens_for_model(model: str) -> int:
    """Get max output tokens based on model."""
    lower = model.lower()
    if "opus" in lower:
        return MAX_OUTPUT_TOKENS_THINKING
    return MAX_OUTPUT_TOKENS_DEFAULT


def build_system_prompt_blocks(
    system_prompt: list[str],
) -> str | list[dict[str, Any]]:
    """Build system prompt blocks for the API."""
    if not system_prompt:
        return ""
    if len(system_prompt) == 1:
        return system_prompt[0]
    return [{"type": "text", "text": s} for s in system_prompt]


def build_tools_param(tools: Sequence[Any]) -> list[dict[str, Any]]:
    """Build tools parameter for API call."""
    result = []
    for tool in tools:
        schema = (
            tool.input_schema() if callable(getattr(tool, "input_schema", None)) else {}
        )
        result.append(
            {
                "name": tool.name,
                "description": getattr(tool, "search_hint", tool.name),
                "input_schema": schema,
            }
        )
    return result


def call_model_api(
    *,
    messages: list[dict[str, Any]],
    system_prompt: list[str],
    model: str,
    tools: Sequence[Any],
    thinking_config: Optional[dict[str, Any]] = None,
    max_tokens: Optional[int] = None,
    stream: bool = True,
    fallback_model: Optional[str] = None,
    on_streaming_fallback: Optional[Callable[[], None]] = None,
) -> Any:
    """
    Call the Anthropic Messages API.

    Mirrors queryModelWithStreaming() / queryModel() in hare.ts.
    Uses the official anthropic Python SDK.
    """

    async def _build_context() -> Any:
        api_model = (
            normalize_model_string_for_api(model)
            if model
            else "claude-sonnet-4-6-20260301"
        )

        try:
            import anthropic
        except ImportError:
            return _error_response(
                "anthropic package is not installed. Run: pip install anthropic"
            )

        base_url = _normalize_anthropic_base_url(os.environ.get("ANTHROPIC_BASE_URL"))
        default_headers = _get_default_headers()
        auth_token = os.environ.get("ANTHROPIC_AUTH_TOKEN") or None
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")

        if not auth_token:
            auth_token = _get_api_key_from_api_key_helper()
        if not auth_token:
            auth_token = _auth_token_from_authorization_header(
                default_headers.get("Authorization")
            )
        if not api_key:
            api_key = _header_value(default_headers, "x-api-key")

        # Match the recovered JS behavior more closely:
        # - direct API key auth when ANTHROPIC_API_KEY exists
        # - bearer-token/custom-header auth for Anthropic-compatible gateways
        # - allow custom base_url without requiring the Anthropic API key path
        if not api_key and not auth_token and "Authorization" not in default_headers:
            return _error_response(
                "Neither ANTHROPIC_API_KEY nor ANTHROPIC_AUTH_TOKEN/apiKeyHelper/custom Authorization header is set."
            )

        client_kwargs: dict[str, Any] = {
            **({"base_url": base_url} if base_url else {}),
            **({"default_headers": default_headers} if default_headers else {}),
        }
        if api_key:
            client_kwargs["api_key"] = api_key
        if auth_token:
            client_kwargs["auth_token"] = auth_token

        client = anthropic.AsyncAnthropic(**client_kwargs)

        effective_max_tokens = max_tokens or get_max_output_tokens_for_model(api_model)
        system_block = build_system_prompt_blocks(system_prompt)
        tools_param = build_tools_param(tools) if tools else []

        kwargs: dict[str, Any] = {
            "model": api_model,
            "max_tokens": effective_max_tokens,
            "messages": messages,
        }

        if system_block:
            kwargs["system"] = system_block
        if tools_param:
            kwargs["tools"] = tools_param
        if thinking_config:
            kwargs["thinking"] = thinking_config

        betas = _get_betas(api_model)
        if betas:
            kwargs["betas"] = betas

        start_time = time.time()
        return client, kwargs, start_time

    async def _non_stream_runner() -> Any:
        built = await _build_context()
        if isinstance(built, AssistantMessage):
            return built
        client, built_kwargs, start_time = built
        try:
            return await _non_streaming_request_with_retries(
                client=client,
                kwargs=built_kwargs,
                model=model,
                fallback_model=fallback_model,
            )
        finally:
            duration = time.time() - start_time
            from hare.cost_tracker import add_api_duration

            add_api_duration(duration)

    async def _stream_runner() -> AsyncGenerator[AssistantMessage | StreamEvent, None]:
        built = await _build_context()
        if isinstance(built, AssistantMessage):
            yield built
            return
        client, built_kwargs, start_time = built
        try:
            async for item in _streaming_request_with_retries(
                client=client,
                kwargs=built_kwargs,
                model=model,
                fallback_model=fallback_model,
                on_streaming_fallback=on_streaming_fallback,
            ):
                yield item
        finally:
            duration = time.time() - start_time
            from hare.cost_tracker import add_api_duration

            add_api_duration(duration)

    if stream:
        return _stream_runner()
    return _non_stream_runner()


def _get_default_headers() -> dict[str, str]:
    headers: dict[str, str] = {
        "x-app": "cli",
        "User-Agent": f"hare/{VERSION}",
        "X-Claude-Code-Session-Id": get_session_id(),
    }
    container_id = os.environ.get("CLAUDE_CODE_CONTAINER_ID")
    remote_session_id = os.environ.get("CLAUDE_CODE_REMOTE_SESSION_ID")
    client_app = os.environ.get("CLAUDE_AGENT_SDK_CLIENT_APP")
    if container_id:
        headers["x-claude-remote-container-id"] = container_id
    if remote_session_id:
        headers["x-claude-remote-session-id"] = remote_session_id
    if client_app:
        headers["x-client-app"] = client_app
    if is_env_truthy(os.environ.get("CLAUDE_CODE_ADDITIONAL_PROTECTION")):
        headers["x-anthropic-additional-protection"] = "true"

    raw = os.environ.get("ANTHROPIC_CUSTOM_HEADERS", "")
    if not raw:
        return headers

    for line in raw.splitlines():
        if not line.strip():
            continue
        if ":" not in line:
            continue
        name, value = line.split(":", 1)
        name = name.strip()
        value = value.strip()
        if name:
            headers[name] = value
    return headers


def _normalize_anthropic_base_url(base_url: str | None) -> str | None:
    """Accept common provider base URLs while still using Anthropic SDK paths."""
    if not base_url:
        return None
    trimmed = base_url.rstrip("/")
    try:
        parsed = urlparse(trimmed)
    except Exception:
        return trimmed
    if parsed.hostname == "api.deepseek.com" and parsed.path in ("", "/"):
        return f"{trimmed}/anthropic"
    return trimmed


def _header_value(headers: dict[str, str], wanted: str) -> str:
    for name, value in headers.items():
        if name.lower() == wanted.lower():
            return value
    return ""


def _auth_token_from_authorization_header(value: str | None) -> str | None:
    if not value:
        return None
    trimmed = value.strip()
    lower = trimmed.lower()
    if lower.startswith("bearer "):
        token = trimmed[7:].strip()
        return token or None
    return trimmed or None


def _get_api_key_from_api_key_helper() -> str | None:
    try:
        from hare.utils.cwd import get_cwd
        from hare.utils.settings.settings import get_initial_settings

        helper = (get_initial_settings(project_dir=get_cwd()) or {}).get("apiKeyHelper")
    except Exception:
        helper = None
    if not isinstance(helper, str) or not helper.strip():
        return None
    try:
        result = subprocess.run(
            helper,
            shell=True,  # nosec B602
            capture_output=True,
            text=True,
            timeout=10 * 60,
            check=False,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


async def _non_streaming_request(
    client: Any,
    kwargs: dict[str, Any],
) -> AssistantMessage:
    """Execute a non-streaming API request."""
    kwargs.pop("stream", None)
    response = await client.messages.create(**kwargs)

    content_blocks: list[dict[str, Any]] = []
    for block in response.content:
        if block.type == "text":
            content_blocks.append({"type": "text", "text": block.text})
        elif block.type == "tool_use":
            content_blocks.append(
                {
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                }
            )
        elif block.type == "thinking":
            thinking_block: dict[str, Any] = {
                "type": "thinking",
                "thinking": getattr(block, "thinking", ""),
                "signature": getattr(block, "signature", ""),
            }
            citations = getattr(block, "citations", None)
            if citations is not None:
                thinking_block["citations"] = citations
            content_blocks.append(thinking_block)
        elif block.type == "redacted_thinking":
            content_blocks.append(
                {
                    "type": "redacted_thinking",
                    "data": getattr(block, "data", ""),
                }
            )

    usage = NonNullableUsage(
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
    )

    from hare.cost_tracker import add_usage

    add_usage(usage)

    return AssistantMessage(
        type="assistant",
        uuid=str(uuid4()),
        message=APIMessage(
            role="assistant",
            content=content_blocks,
            id=getattr(response, "id", None),
            stop_reason=response.stop_reason or "end_turn",
            usage={
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
            },
        ),
    )


async def _non_streaming_request_with_retries(
    *,
    client: Any,
    kwargs: dict[str, Any],
    model: str,
    fallback_model: Optional[str],
    initial_consecutive_529_errors: int = 0,
) -> AssistantMessage:
    consecutive_529_errors = initial_consecutive_529_errors
    for attempt in range(1, MAX_RETRIES + 2):
        try:
            return await _non_streaming_request(client, kwargs)
        except Exception as e:
            status = _extract_status_code(e)
            if status == 529:
                consecutive_529_errors += 1
                if consecutive_529_errors >= MAX_529_RETRIES and fallback_model:
                    raise FallbackTriggeredError(model, fallback_model) from e
            else:
                consecutive_529_errors = 0

            if attempt >= MAX_RETRIES + 1 or not _is_retryable_status(status):
                raise
            await _sleep_before_retry(_extract_retry_after_seconds(e), attempt)


async def _streaming_request_events(
    client: Any,
    kwargs: dict[str, Any],
) -> AsyncGenerator[AssistantMessage | StreamEvent, None]:
    usage = NonNullableUsage()
    stop_reason: Optional[str] = None
    message_id: Optional[str] = None
    block_snapshots: dict[int, dict[str, Any]] = {}
    new_messages: list[AssistantMessage] = []
    saw_message_start = False

    async with client.messages.stream(**kwargs) as stream:
        async for event in stream:
            event_type = getattr(event, "type", "")

            if event_type == "message_start":
                saw_message_start = True
                message = getattr(event, "message", None)
                message_id = getattr(message, "id", None) if message is not None else None
                if message is not None and getattr(message, "usage", None) is not None:
                    _copy_usage_from_sdk(usage, message.usage)
            elif event_type == "content_block_start":
                idx = int(getattr(event, "index", len(block_snapshots)))
                block = getattr(event, "content_block", None)
                snapshot = _sdk_block_to_dict(block)
                block_snapshots[idx] = snapshot
            elif event_type == "content_block_delta":
                idx = int(getattr(event, "index", -1))
                if idx >= 0:
                    block_snapshots[idx] = _apply_delta_to_snapshot(
                        block_snapshots.get(idx, {"type": "text", "text": ""}),
                        getattr(event, "delta", None),
                    )
            elif event_type == "content_block_stop":
                idx = int(getattr(event, "index", -1))
                if idx >= 0 and idx in block_snapshots:
                    block = _finalize_snapshot(block_snapshots[idx])
                    message = _build_streaming_assistant_message(
                        message_uuid=str(uuid4()),
                        message_id=message_id,
                        content=[block],
                        stop_reason=stop_reason,
                        usage=_usage_dict(usage),
                    )
                    new_messages.append(message)
                    yield message
            elif event_type == "message_delta":
                delta = getattr(event, "delta", None)
                if delta is not None:
                    stop_reason = (
                        getattr(delta, "stop_reason", stop_reason) or stop_reason
                    )
                raw_usage = getattr(event, "usage", None)
                if raw_usage is not None:
                    _copy_usage_from_sdk(usage, raw_usage)
                if new_messages:
                    last_message = new_messages[-1]
                    last_message.message.usage = _usage_dict(usage)
                    last_message.message.stop_reason = stop_reason
                if stop_reason == "max_tokens":
                    max_tokens = kwargs.get("max_tokens", "")
                    yield create_assistant_api_error_message(
                        content=(
                            f"{API_ERROR_MESSAGE_PREFIX}: Hare's response exceeded the "
                            f"{max_tokens} output token maximum. To configure this behavior, "
                            "set the CLAUDE_CODE_MAX_OUTPUT_TOKENS environment variable."
                        ),
                        error="max_output_tokens",
                    )
                if stop_reason == "refusal":
                    yield _create_refusal_message(str(kwargs.get("model", "")))
                if stop_reason == "model_context_window_exceeded":
                    yield create_assistant_api_error_message(
                        content=(
                            f"{API_ERROR_MESSAGE_PREFIX}: The model has reached its context "
                            "window limit."
                        ),
                        error="max_output_tokens",
                    )
            elif event_type == "message_stop":
                pass

            yield StreamEvent(type="stream_event", event=_serialize_stream_event(event))

    if not saw_message_start:
        raise RuntimeError("Stream ended without receiving any events")
    if len(new_messages) == 0 and not stop_reason:
        raise RuntimeError(
            "Stream completed with message_start but no content blocks completed"
        )
    from hare.cost_tracker import add_usage

    add_usage(usage)


async def _streaming_request_with_retries(
    *,
    client: Any,
    kwargs: dict[str, Any],
    model: str,
    fallback_model: Optional[str],
    on_streaming_fallback: Optional[Callable[[], None]],
) -> AsyncGenerator[AssistantMessage | StreamEvent, None]:
    consecutive_529_errors = 0
    streaming_error: Optional[Exception] = None
    did_fallback_to_non_streaming = False

    for attempt in range(1, MAX_RETRIES + 2):
        try:
            async for item in _streaming_request_events(client, kwargs):
                yield item
            return
        except Exception as e:
            streaming_error = e
            status = _extract_status_code(e)
            if status == 529:
                consecutive_529_errors += 1
                if consecutive_529_errors >= MAX_529_RETRIES and fallback_model:
                    if callable(on_streaming_fallback):
                        on_streaming_fallback()
                    raise FallbackTriggeredError(model, fallback_model) from e
            else:
                consecutive_529_errors = 0

            if not did_fallback_to_non_streaming and _extract_status_code(e) == 404:
                if callable(on_streaming_fallback):
                    on_streaming_fallback()
                did_fallback_to_non_streaming = True
                result = await _non_streaming_request_with_retries(
                    client=client,
                    kwargs=kwargs,
                    model=model,
                    fallback_model=fallback_model,
                    initial_consecutive_529_errors=0,
                )
                yield result
                return

            if attempt >= MAX_RETRIES + 1 or not _is_retryable_status(status):
                break
            await _sleep_before_retry(_extract_retry_after_seconds(e), attempt)

    if streaming_error is not None:
        disable_fallback = is_env_truthy(
            os.environ.get("CLAUDE_CODE_DISABLE_NONSTREAMING_FALLBACK")
        ) or bool(
            get_feature_value_cached_may_be_stale(
                "tengu_disable_streaming_to_non_streaming_fallback",
                False,
            )
        )
        if disable_fallback:
            raise streaming_error
        if callable(on_streaming_fallback):
            on_streaming_fallback()
        result = await _non_streaming_request_with_retries(
            client=client,
            kwargs=kwargs,
            model=model,
            fallback_model=fallback_model,
            initial_consecutive_529_errors=1
            if _extract_status_code(streaming_error) == 529
            else 0,
        )
        yield result
        return

    raise RuntimeError("Streaming request exited without result or error")


def _error_response(message: str) -> AssistantMessage:
    """Create an error response message."""
    return AssistantMessage(
        type="assistant",
        uuid=str(uuid4()),
        message=APIMessage(
            role="assistant",
            content=[{"type": "text", "text": message}],
            stop_reason="end_turn",
        ),
    )


def _get_betas(model: str) -> list[str]:
    """Get beta features to enable for this model."""
    betas: list[str] = []
    # Prompt caching is generally available now
    return betas


def _sdk_block_to_dict(block: Any) -> dict[str, Any]:
    if block is None:
        return {"type": "text", "text": ""}
    block_type = getattr(block, "type", "")
    if block_type == "text":
        return {"type": "text", "text": getattr(block, "text", "")}
    if block_type == "tool_use":
        return {
            "type": "tool_use",
            "id": getattr(block, "id", ""),
            "name": getattr(block, "name", ""),
            "input": getattr(block, "input", {}),
        }
    if block_type == "thinking":
        out: dict[str, Any] = {
            "type": "thinking",
            "thinking": getattr(block, "thinking", ""),
            "signature": getattr(block, "signature", ""),
        }
        citations = getattr(block, "citations", None)
        if citations is not None:
            out["citations"] = citations
        return out
    if block_type == "redacted_thinking":
        return {"type": "redacted_thinking", "data": getattr(block, "data", "")}
    return {"type": block_type or "text", "text": getattr(block, "text", "")}


def _apply_delta_to_snapshot(snapshot: dict[str, Any], delta: Any) -> dict[str, Any]:
    if delta is None:
        return snapshot
    delta_type = getattr(delta, "type", "")
    next_snapshot = dict(snapshot)
    if delta_type == "text_delta":
        next_snapshot["type"] = "text"
        next_snapshot["text"] = str(next_snapshot.get("text", "")) + getattr(
            delta, "text", ""
        )
    elif delta_type == "thinking_delta":
        next_snapshot["type"] = "thinking"
        next_snapshot["thinking"] = str(next_snapshot.get("thinking", "")) + getattr(
            delta, "thinking", ""
        )
    elif delta_type == "signature_delta":
        next_snapshot["type"] = "thinking"
        next_snapshot["signature"] = getattr(delta, "signature", "")
    elif delta_type == "input_json_delta":
        next_snapshot["type"] = "tool_use"
        partial_json = getattr(delta, "partial_json", None)
        if partial_json is not None:
            next_snapshot["_partial_json"] = (
                str(next_snapshot.get("_partial_json", "")) + partial_json
            )
        snapshot_obj = getattr(delta, "snapshot", None)
        if snapshot_obj is not None:
            next_snapshot["input"] = snapshot_obj
    return next_snapshot


def _content_list_from_snapshots(
    block_snapshots: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for idx in sorted(block_snapshots.keys()):
        block = _finalize_snapshot(block_snapshots[idx])
        out.append(block)
    return out


def _finalize_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    block = dict(snapshot)
    partial_json = block.pop("_partial_json", None)
    if (
        block.get("type") == "tool_use"
        and partial_json
        and (not isinstance(block.get("input"), dict) or not block.get("input"))
    ):
        try:
            parsed = json.loads(partial_json)
            if isinstance(parsed, dict):
                block["input"] = parsed
        except Exception:
            pass
    return block


def _copy_usage_from_sdk(usage: NonNullableUsage, raw_usage: Any) -> None:
    input_tokens = getattr(raw_usage, "input_tokens", None)
    if isinstance(input_tokens, int) and input_tokens > 0:
        usage.input_tokens = input_tokens

    cache_creation_input_tokens = getattr(
        raw_usage, "cache_creation_input_tokens", None
    )
    if isinstance(cache_creation_input_tokens, int) and cache_creation_input_tokens > 0:
        usage.cache_creation_input_tokens = cache_creation_input_tokens

    cache_read_input_tokens = getattr(raw_usage, "cache_read_input_tokens", None)
    if isinstance(cache_read_input_tokens, int) and cache_read_input_tokens > 0:
        usage.cache_read_input_tokens = cache_read_input_tokens

    output_tokens = getattr(raw_usage, "output_tokens", None)
    if isinstance(output_tokens, int):
        usage.output_tokens = output_tokens

    cache_deleted_input_tokens = getattr(raw_usage, "cache_deleted_input_tokens", None)
    if isinstance(cache_deleted_input_tokens, int) and cache_deleted_input_tokens > 0:
        setattr(usage, "cache_deleted_input_tokens", cache_deleted_input_tokens)


def _usage_dict(usage: NonNullableUsage) -> dict[str, int]:
    return {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cache_creation_input_tokens": usage.cache_creation_input_tokens,
        "cache_read_input_tokens": usage.cache_read_input_tokens,
        "cache_deleted_input_tokens": int(
            getattr(usage, "cache_deleted_input_tokens", 0) or 0
        ),
    }


def _build_streaming_assistant_message(
    *,
    message_uuid: str,
    message_id: str | None,
    content: list[dict[str, Any]],
    stop_reason: Optional[str],
    usage: dict[str, int],
) -> AssistantMessage:
    return AssistantMessage(
        type="assistant",
        uuid=message_uuid,
        message=APIMessage(
            role="assistant",
            content=[dict(block) for block in content],
            id=message_id,
            stop_reason=stop_reason,
            usage=usage,
        ),
    )


def _create_refusal_message(model: str) -> AssistantMessage:
    base_message = (
        f"{API_ERROR_MESSAGE_PREFIX}: Hare is unable to respond to this request, "
        "which appears to violate our Usage Policy (https://www.anthropic.com/legal/aup). "
    )
    if get_is_non_interactive_session():
        base_message += "Try rephrasing the request or attempting a different approach."
    else:
        base_message += (
            "Please double press esc to edit your last message or start a new "
            "session for Hare to assist with a different task."
        )
    if model != "claude-sonnet-4-20250514":
        base_message += (
            " If you are seeing this refusal repeatedly, try running "
            "/model claude-sonnet-4-20250514 to switch models."
        )
    return create_assistant_api_error_message(
        content=base_message,
        error="invalid_request",
    )


def _serialize_stream_event(event: Any) -> dict[str, Any]:
    if hasattr(event, "model_dump"):
        try:
            return event.model_dump()
        except Exception:
            pass
    if hasattr(event, "__dict__"):
        out = {}
        for key, value in vars(event).items():
            out[key] = _serialize_stream_value(value)
        return out
    return {"type": getattr(event, "type", "unknown")}


def _serialize_stream_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_serialize_stream_value(v) for v in value]
    if isinstance(value, dict):
        return {k: _serialize_stream_value(v) for k, v in value.items()}
    if hasattr(value, "model_dump"):
        try:
            return value.model_dump()
        except Exception:
            return str(value)
    if hasattr(value, "__dict__"):
        return {k: _serialize_stream_value(v) for k, v in vars(value).items()}
    return str(value)


def _extract_status_code(error: Exception) -> int:
    if isinstance(error, OverloadedError):
        return 529
    if isinstance(error, RateLimitError):
        return 429
    status = getattr(error, "status_code", None)
    if isinstance(status, int):
        return status
    status = getattr(error, "status", None)
    if isinstance(status, int):
        return status
    message = str(error).lower()
    if "529" in message or "overloaded" in message:
        return 529
    if "429" in message or "rate limit" in message:
        return 429
    if "503" in message:
        return 503
    if "502" in message:
        return 502
    return 0


def _extract_retry_after_seconds(error: Exception) -> Optional[float]:
    retry_after = getattr(error, "retry_after", None)
    if isinstance(retry_after, (int, float)) and retry_after > 0:
        return float(retry_after)
    headers = getattr(error, "headers", None)
    if headers is not None:
        getter = getattr(headers, "get", None)
        if callable(getter):
            value = getter("retry-after")
            if value is not None:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    return None
    return None


def _is_retryable_status(status: int) -> bool:
    return status in (429, 502, 503, 529)


async def _sleep_before_retry(retry_after: Optional[float], attempt: int) -> None:
    import asyncio

    if retry_after is not None and retry_after > 0:
        await asyncio.sleep(retry_after)
        return
    await asyncio.sleep(BASE_RETRY_DELAY_SECONDS * attempt)
