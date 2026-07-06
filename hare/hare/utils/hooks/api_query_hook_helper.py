"""
Factory for API-style sampling hooks.

Port of: src/utils/hooks/apiQueryHookHelper.ts
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Generic, TypeVar

TResult = TypeVar("TResult")


@dataclass
class ApiQueryHookContext:
    """Extended REPL hook context (queryMessageCount optional)."""

    query_message_count: int | None = None
    raw: dict[str, Any] | None = None


@dataclass
class ApiQuerySuccess(Generic[TResult]):
    type: str = "success"
    query_name: str = ""
    result: Any = None
    message_id: str = ""
    model: str = ""
    uuid: str = ""


@dataclass
class ApiQueryError:
    type: str = "error"
    query_name: str = ""
    error: str = ""


ApiQueryResult = ApiQuerySuccess[Any] | ApiQueryError


@dataclass
class ApiQueryHookConfig(Generic[TResult]):
    name: str
    should_run: Callable[[ApiQueryHookContext], Awaitable[bool]]
    build_messages: Callable[[ApiQueryHookContext], list[dict[str, Any]]]
    system_prompt: str | None = None
    use_tools: bool = True
    parse_response: Callable[[str, ApiQueryHookContext], TResult] | None = None
    log_result: Callable[[ApiQueryResult, ApiQueryHookContext], None] | None = None
    get_model: Callable[[ApiQueryHookContext], str] | None = None


async def create_api_query_hook(
    config: ApiQueryHookConfig[Any],
) -> Callable[[ApiQueryHookContext], Awaitable[ApiQueryResult]]:
    """Return async runner that executes the configured query (stub backend)."""

    async def run(context: ApiQueryHookContext) -> ApiQueryResult:
        if not await config.should_run(context):
            return ApiQueryError(query_name=config.name, error="skipped")
        # Wire queryModelWithoutStreaming in production.
        return ApiQuerySuccess(
            query_name=config.name,
            result=None,
            message_id="",
            model=config.get_model(context) if config.get_model else "",
            uuid=str(uuid.uuid4()),
        )

    return run
