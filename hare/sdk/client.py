"""Thin SDK facade over QueryEngine.

This intentionally stays close to the recovered QueryEngine shape and only
provides a small public wrapper for Python callers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, AsyncGenerator, Optional

from ..commands import get_commands
from ..query_engine import QueryEngine, QueryEngineConfig
from ..tool import get_empty_tool_permission_context
from ..tools import get_tools
from ..utils.cwd import get_cwd
from ..app_types.permissions import PermissionAllowDecision


async def _sdk_default_can_use_tool(
    tool: Any,
    input: Any,
    context: Any,
    assistant_msg: Any,
    tool_use_id: str,
    force: Any,
) -> Any:
    return PermissionAllowDecision(behavior="allow", updated_input=input)


@dataclass
class HareClientOptions:
    cwd: Optional[str] = None
    model: Optional[str] = None
    max_turns: Optional[int] = None
    verbose: bool = False
    system_prompt: Optional[str] = None
    append_system_prompt: Optional[str] = None


class HareClient:
    """Thin public SDK wrapper around a single QueryEngine instance."""

    def __init__(self, engine: QueryEngine) -> None:
        self._engine = engine

    @classmethod
    async def create(cls, options: HareClientOptions | None = None) -> "HareClient":
        opts = options or HareClientOptions()
        cwd = opts.cwd or get_cwd()
        permission_context = get_empty_tool_permission_context()
        tools = get_tools(permission_context)
        commands = await get_commands(cwd)
        engine = QueryEngine(
            QueryEngineConfig(
                cwd=cwd,
                tools=tools,
                commands=commands,
                can_use_tool=_sdk_default_can_use_tool,
                get_app_state=lambda: {},
                set_app_state=lambda _f: None,
                user_specified_model=opts.model,
                max_turns=opts.max_turns,
                verbose=opts.verbose,
                custom_system_prompt=opts.system_prompt,
                append_system_prompt=opts.append_system_prompt,
            )
        )
        return cls(engine)

    async def stream(
        self,
        prompt: str | list[Any],
        *,
        uuid: str | None = None,
        is_meta: bool = False,
    ) -> AsyncGenerator[dict[str, Any], None]:
        async for event in self._engine.submit_message(
            prompt,
            uuid=uuid,
            is_meta=is_meta,
        ):
            yield dict(event)

    async def ask(
        self,
        prompt: str | list[Any],
        *,
        uuid: str | None = None,
        is_meta: bool = False,
    ) -> dict[str, Any]:
        final: dict[str, Any] | None = None
        async for event in self.stream(prompt, uuid=uuid, is_meta=is_meta):
            if event.get("type") == "result":
                final = event
        return final or {"type": "result", "subtype": "empty", "result": ""}

    def interrupt(self) -> None:
        self._engine.interrupt()

    def resume(self) -> QueryEngine:
        """Expose the underlying engine for advanced callers resuming stateful sessions."""
        return self._engine

    @property
    def engine(self) -> QueryEngine:
        return self._engine
