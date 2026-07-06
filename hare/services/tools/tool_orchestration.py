"""Tool-call partitioning and concurrent/serial execution.

Port of: src/services/tools/toolOrchestration.ts (line-by-line; 188 lines TS).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Callable, Optional

from hare.services.tools.tool_execution import MessageUpdateLazy, run_tool_use
from hare.tool import CanUseToolFn, ToolUseContext, find_tool_by_name
from hare.app_types.message import AssistantMessage, Message
from hare.utils.generators import all as gen_all

# `ToolUseBlock` from @anthropic-ai/sdk is just an object with `id`, `name`,
# `input`. In Python we accept any dict-shaped value (or a TypedDict if
# callers want stricter typing).
ToolUseBlock = dict[str, Any]


# -- src/services/tools/toolOrchestration.ts L8-12
def _get_max_tool_use_concurrency() -> int:
    raw = os.environ.get("CLAUDE_CODE_MAX_TOOL_USE_CONCURRENCY", "")
    try:
        parsed = int(raw, 10)
    except ValueError:
        parsed = 0
    return parsed if parsed else 10


# -- src/services/tools/toolOrchestration.ts L14-17
@dataclass
class MessageUpdate:
    new_context: ToolUseContext
    message: Optional[Message] = None


# -- src/services/tools/toolOrchestration.ts L19-82
async def run_tools(
    tool_use_messages: list[ToolUseBlock],
    assistant_messages: list[AssistantMessage],
    can_use_tool: CanUseToolFn,
    tool_use_context: ToolUseContext,
) -> AsyncGenerator[MessageUpdate, None]:
    current_context = tool_use_context
    for batch in _partition_tool_calls(tool_use_messages, current_context):
        is_concurrency_safe = batch.is_concurrency_safe
        blocks = batch.blocks
        if is_concurrency_safe:
            queued_context_modifiers: dict[
                str, list[Callable[[ToolUseContext], ToolUseContext]]
            ] = {}
            # Run read-only batch concurrently
            async for update in _run_tools_concurrently(
                blocks,
                assistant_messages,
                can_use_tool,
                current_context,
            ):
                if update.context_modifier is not None:
                    tool_use_id = update.context_modifier.tool_use_id
                    modify_context = update.context_modifier.modify_context
                    if tool_use_id not in queued_context_modifiers:
                        queued_context_modifiers[tool_use_id] = []
                    queued_context_modifiers[tool_use_id].append(modify_context)
                yield MessageUpdate(
                    message=update.message,
                    new_context=current_context,
                )
            for block in blocks:
                modifiers = queued_context_modifiers.get(block["id"])
                if not modifiers:
                    continue
                for modifier in modifiers:
                    current_context = modifier(current_context)
            yield MessageUpdate(new_context=current_context)
        else:
            # Run non-read-only batch serially
            async for update in _run_tools_serially(
                blocks,
                assistant_messages,
                can_use_tool,
                current_context,
            ):
                if update.new_context is not None:
                    current_context = update.new_context
                yield MessageUpdate(
                    message=update.message,
                    new_context=current_context,
                )


# -- src/services/tools/toolOrchestration.ts L84
@dataclass
class _Batch:
    is_concurrency_safe: bool
    blocks: list[ToolUseBlock] = field(default_factory=list)


# -- src/services/tools/toolOrchestration.ts L86-116
def _partition_tool_calls(
    tool_use_messages: list[ToolUseBlock],
    tool_use_context: ToolUseContext,
) -> list[_Batch]:
    """Partition tool calls into batches where each batch is either:
    1. A single non-read-only tool, or
    2. Multiple consecutive read-only tools
    """
    acc: list[_Batch] = []
    for tool_use in tool_use_messages:
        tool = find_tool_by_name(tool_use_context.options.tools, tool_use["name"])
        parsed_input = _safe_parse_tool_input(tool, tool_use.get("input"))
        if tool is None or parsed_input is _INVALID_TOOL_INPUT:
            is_concurrency_safe = False
        else:
            try:
                is_concurrency_safe = bool(tool.is_concurrency_safe(parsed_input))
            except Exception:  # noqa: BLE001
                # If isConcurrencySafe throws (e.g., due to shell-quote parse
                # failure), treat as not concurrency-safe to be conservative.
                is_concurrency_safe = False
        if is_concurrency_safe and acc and acc[-1].is_concurrency_safe:
            acc[-1].blocks.append(tool_use)
        else:
            acc.append(
                _Batch(is_concurrency_safe=is_concurrency_safe, blocks=[tool_use])
            )
    return acc


# -- src/services/tools/toolOrchestration.ts L118-150
async def _run_tools_serially(
    tool_use_messages: list[ToolUseBlock],
    assistant_messages: list[AssistantMessage],
    can_use_tool: CanUseToolFn,
    tool_use_context: ToolUseContext,
) -> AsyncGenerator[MessageUpdate, None]:
    current_context = tool_use_context

    for tool_use in tool_use_messages:
        if tool_use_context.set_in_progress_tool_use_ids is not None:
            tool_use_context.set_in_progress_tool_use_ids(
                lambda prev, tid=tool_use["id"]: {*prev, tid}
            )
        async for update in run_tool_use(
            tool_use,
            _find_owning_assistant(assistant_messages, tool_use["id"]),
            can_use_tool,
            current_context,
        ):
            if update.context_modifier is not None:
                current_context = update.context_modifier.modify_context(
                    current_context
                )
            yield MessageUpdate(
                message=update.message,
                new_context=current_context,
            )
        _mark_tool_use_as_complete(tool_use_context, tool_use["id"])


# -- src/services/tools/toolOrchestration.ts L152-177
async def _run_tools_concurrently(
    tool_use_messages: list[ToolUseBlock],
    assistant_messages: list[AssistantMessage],
    can_use_tool: CanUseToolFn,
    tool_use_context: ToolUseContext,
) -> AsyncGenerator[MessageUpdateLazy, None]:
    async def _per_tool(
        tool_use: ToolUseBlock,
    ) -> AsyncGenerator[MessageUpdateLazy, None]:
        if tool_use_context.set_in_progress_tool_use_ids is not None:
            tool_use_context.set_in_progress_tool_use_ids(
                lambda prev, tid=tool_use["id"]: {*prev, tid}
            )
        async for update in run_tool_use(
            tool_use,
            _find_owning_assistant(assistant_messages, tool_use["id"]),
            can_use_tool,
            tool_use_context,
        ):
            yield update
        _mark_tool_use_as_complete(tool_use_context, tool_use["id"])

    generators = [_per_tool(t) for t in tool_use_messages]
    async for value in gen_all(generators, _get_max_tool_use_concurrency()):
        yield value


# -- src/services/tools/toolOrchestration.ts L179-188
def _mark_tool_use_as_complete(
    tool_use_context: ToolUseContext,
    tool_use_id: str,
) -> None:
    if tool_use_context.set_in_progress_tool_use_ids is None:
        return

    def _without(prev: set[str]) -> set[str]:
        next_set = set(prev)
        next_set.discard(tool_use_id)
        return next_set

    tool_use_context.set_in_progress_tool_use_ids(_without)


# -- helper for `assistantMessages.find(_ => _.message.content.some(...))`
# (TS L132-136 / L165-169). Extracted to keep the runners isomorphic.
def _find_owning_assistant(
    assistant_messages: list[AssistantMessage],
    tool_use_id: str,
) -> AssistantMessage:
    for msg in assistant_messages:
        content = getattr(getattr(msg, "message", None), "content", None)
        if not isinstance(content, list):
            continue
        for block in content:
            block_type = (
                block.get("type")
                if isinstance(block, dict)
                else getattr(block, "type", None)
            )
            block_id = (
                block.get("id")
                if isinstance(block, dict)
                else getattr(block, "id", None)
            )
            if block_type == "tool_use" and block_id == tool_use_id:
                return msg
    # TS uses non-null assertion `!`; mirror with a clear runtime error.
    raise LookupError(f"No assistant message owns tool_use id={tool_use_id!r}")


_INVALID_TOOL_INPUT = object()


def _safe_parse_tool_input(tool: Any, raw_input: Any) -> dict[str, Any] | object:
    if tool is None or not isinstance(raw_input, dict):
        return _INVALID_TOOL_INPUT

    schema_getter = getattr(tool, "input_schema", None)
    if not callable(schema_getter):
        return raw_input

    schema = schema_getter() or {}
    if not isinstance(schema, dict):
        return raw_input

    return raw_input if _matches_schema(raw_input, schema) else _INVALID_TOOL_INPUT


def _matches_schema(value: dict[str, Any], schema: dict[str, Any]) -> bool:
    schema_type = schema.get("type")
    if schema_type == "object" and not isinstance(value, dict):
        return False

    required = schema.get("required", [])
    if isinstance(required, list):
        for key in required:
            if isinstance(key, str) and key not in value:
                return False

    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return True

    for key, prop_schema in properties.items():
        if key not in value or not isinstance(prop_schema, dict):
            continue
        if not _matches_schema_value(value[key], prop_schema):
            return False
    return True


def _matches_schema_value(value: Any, schema: dict[str, Any]) -> bool:
    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        return any(_matches_schema_value(value, {"type": item}) for item in schema_type)
    if schema_type == "string":
        return isinstance(value, str)
    if schema_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if schema_type == "number":
        return (isinstance(value, int) or isinstance(value, float)) and not isinstance(
            value, bool
        )
    if schema_type == "boolean":
        return isinstance(value, bool)
    if schema_type == "array":
        return isinstance(value, list)
    if schema_type == "object":
        return isinstance(value, dict) and _matches_schema(value, schema)
    if schema_type in {"null", None}:
        return value is None if schema_type == "null" else True
    return True
