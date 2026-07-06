"""
Tool registry and tool pool assembly.

Port of: src/tools.ts

This is the central registry for all built-in tools. It maps exactly
to getAllBaseTools() / getTools() / assembleToolPool() in the TS source.
"""

from __future__ import annotations

import importlib
import os
from typing import Any, Sequence

from hare.tool import (
    Tool,
    ToolBase,
    ToolResult,
    tool_matches_name,
)
from hare.app_types.permissions import (
    ToolPermissionContext,
)
from hare.utils.env_utils import is_env_truthy

# TS constants.ts: global disallowed tools for all sub-agents
# Prevents: recursive agent spawning, plan mode exit from sub-agents,
# task output reading (creates loops), and user question prompts (sub-agents can't interact)
ALL_AGENT_DISALLOWED_TOOLS: list[str] = [
    "Agent",  # prevent recursive agent spawning
    "TaskOutput",  # prevent task output loops
    "TaskStop",  # sub-agents can't stop tasks
    "AskUserQuestion",  # sub-agents can't prompt users
    "EnterPlanMode",  # plan mode entry is main-thread only
    "ExitPlanMode",  # plan mode exit is main-thread only
]

# TS: additional tools disallowed for custom (non-builtin) agents
CUSTOM_AGENT_DISALLOWED_TOOLS: list[str] = [
    "Skill",  # custom agents can't invoke arbitrary skills
]

# TS: whitelist for async/background agents
ASYNC_AGENT_ALLOWED_TOOLS: list[str] = [
    "Read",
    "Write",
    "Edit",
    "Bash",
    "Grep",
    "Glob",
    "WebSearch",
    "WebFetch",
    "NotebookEdit",
    "TodoWrite",
    "Task",
    # MCP tools are always allowed (checked by prefix in filter function)
]

COORDINATOR_MODE_ALLOWED_TOOLS: list[str] = []

TOOL_PRESETS = ("default",)


def _wrap_module_tool(module_path: str, tool_name: str, **overrides: Any) -> ToolBase:
    """Wrap a function-based tool module into a ToolBase singleton."""
    mod = importlib.import_module(module_path)

    class _Wrapped(ToolBase):
        name = tool_name
        aliases = getattr(mod, "ALIASES", [])
        search_hint = getattr(mod, "SEARCH_HINT", tool_name)

        def input_schema(self) -> dict[str, Any]:
            return mod.input_schema()

        def is_read_only(self, input: dict[str, Any]) -> bool:
            fn = getattr(mod, "is_read_only", None)
            if fn:
                return fn(input)
            return overrides.get("read_only", False)

        def is_destructive(self, input: dict[str, Any]) -> bool:
            fn = getattr(mod, "is_destructive", None)
            if fn:
                return fn(input)
            return overrides.get("destructive", False)

        def validate_input(self, input: dict[str, Any]) -> Any:
            fn = getattr(mod, "validate_input", None)
            if fn:
                result = fn(input)
                return result
            from hare.tool import ValidationResultOK

            return ValidationResultOK()

        def output_schema(self) -> dict[str, Any] | None:
            fn = getattr(mod, "output_schema", None)
            if fn:
                return fn()
            return None

        def inputs_equivalent(self, a: dict[str, Any], b: dict[str, Any]) -> bool:
            fn = getattr(mod, "inputs_equivalent", None)
            if fn:
                return fn(a, b)
            return False

        def map_tool_result_to_tool_result_block_param(
            self, content: Any, tool_use_id: str
        ) -> dict[str, Any]:
            # Let a module shape its own model-visible tool_result content (TS
            # mapToolResultToToolResultBlockParam). Without this, a dict result
            # is str()-ified into a Python-dict repr the model then reads.
            fn = getattr(mod, "map_tool_result_to_tool_result_block_param", None)
            if fn:
                return fn(content, tool_use_id)
            return super().map_tool_result_to_tool_result_block_param(
                content, tool_use_id
            )

        async def call(
            self,
            args: dict[str, Any],
            context: Any = None,
            can_use_tool: Any = None,
            parent_message: Any = None,
            on_progress: Any = None,
            **kw: Any,
        ) -> ToolResult:
            # Pass full ToolUseContext + permission callback to function-based tools.
            # Many function-based tools accept only input params; use inspect to adapt.
            import inspect as _inspect

            try:
                sig = _inspect.signature(mod.call)
                call_kwargs: dict[str, Any] = {}
                param_names = set(sig.parameters.keys())
                # Always pass tool input args
                for k, v in (args or {}).items():
                    if k in param_names:
                        call_kwargs[k] = v
                # Pass optional protocol params if the tool declares them
                if "context" in param_names:
                    call_kwargs["context"] = context
                if "can_use_tool" in param_names:
                    call_kwargs["can_use_tool"] = can_use_tool
                if "parent_message" in param_names:
                    call_kwargs["parent_message"] = parent_message
                if "on_progress" in param_names:
                    call_kwargs["on_progress"] = on_progress
                result = await mod.call(**call_kwargs)
            except (ValueError, TypeError):
                # Fallback: pass only input args for tools that don't use inspect
                result = await mod.call(**args)
            if isinstance(result, dict):
                return ToolResult(data=result)
            return ToolResult(data=result)

    return _Wrapped()


def parse_tool_preset(preset: str) -> str | None:
    lower = preset.lower()
    if lower in TOOL_PRESETS:
        return lower
    return None


def get_tools_for_default_preset() -> list[str]:
    tools = get_all_base_tools()
    return [t.name for t in tools if t.is_enabled()]


def get_all_base_tools() -> list[Tool]:
    """
    Get all tools. Class-based tools are imported directly;
    function-based tool modules are wrapped via _wrap_module_tool.
    """
    from hare.tools_impl.BashTool.bash_tool import BashTool
    from hare.tools_impl.AgentTool.agent_tool import AgentTool
    from hare.tools_impl.TodoWriteTool.todo_write_tool import TodoWriteTool
    from hare.tools_impl.SendMessageTool.send_message_tool import SendMessageTool

    FileReadTool = _wrap_module_tool(
        "hare.tools_impl.FileReadTool.file_read_tool", "Read", read_only=True
    )
    FileEditTool = _wrap_module_tool(
        "hare.tools_impl.FileEditTool.file_edit_tool", "Edit", destructive=True
    )
    FileWriteTool = _wrap_module_tool(
        "hare.tools_impl.FileWriteTool.file_write_tool", "Write", destructive=True
    )
    GlobTool = _wrap_module_tool(
        "hare.tools_impl.GlobTool.glob_tool", "Glob", read_only=True
    )
    GrepTool = _wrap_module_tool(
        "hare.tools_impl.GrepTool.grep_tool", "Grep", read_only=True
    )
    WebFetchTool = _wrap_module_tool(
        "hare.tools_impl.WebFetchTool.web_fetch_tool", "WebFetch", read_only=True
    )
    WebSearchTool = _wrap_module_tool(
        "hare.tools_impl.WebSearchTool.web_search_tool", "WebSearch", read_only=True
    )
    NotebookEditTool = _wrap_module_tool(
        "hare.tools_impl.NotebookEditTool.notebook_edit_tool",
        "NotebookEdit",
        destructive=True,
    )

    tools: list[Tool] = [
        AgentTool,
        BashTool,
        GlobTool,
        GrepTool,
        FileReadTool,
        FileEditTool,
        FileWriteTool,
        WebFetchTool,
        TodoWriteTool,
        WebSearchTool,
        NotebookEditTool,
    ]

    # Tool search — deferred tool discovery (TS tools.ts:207)
    ToolSearchTool = _wrap_module_tool(
        "hare.tools_impl.ToolSearchTool.tool_search_tool", "ToolSearch", read_only=True
    )
    tools.append(ToolSearchTool)

    # Skill invocation — slash-command skill tool
    SkillTool = _wrap_module_tool("hare.tools_impl.SkillTool.skill_tool", "Skill")
    tools.append(SkillTool)

    # Feature-gated tools
    if is_env_truthy(os.environ.get("ENABLE_LSP_TOOL")):
        LSPTool = _wrap_module_tool(
            "hare.tools_impl.LSPTool.lsp_tool", "LSP", read_only=True
        )
        tools.append(LSPTool)

    # Worktree tools
    if is_env_truthy(os.environ.get("ENABLE_WORKTREE")):
        EnterWorktreeTool = _wrap_module_tool(
            "hare.tools_impl.EnterWorktreeTool.enter_worktree_tool", "EnterWorktree"
        )
        ExitWorktreeTool = _wrap_module_tool(
            "hare.tools_impl.ExitWorktreeTool.exit_worktree_tool", "ExitWorktree"
        )
        tools.extend([EnterWorktreeTool, ExitWorktreeTool])

    # Agent swarm tools
    if is_env_truthy(os.environ.get("ENABLE_AGENT_SWARMS")):
        TeamCreateTool = _wrap_module_tool(
            "hare.tools_impl.TeamCreateTool.team_create_tool", "TeamCreate"
        )
        TeamDeleteTool = _wrap_module_tool(
            "hare.tools_impl.TeamDeleteTool.team_delete_tool", "TeamDelete"
        )
        # SendMessage is registered unconditionally above (2.1.88 base set).
        tools.extend([TeamCreateTool, TeamDeleteTool])

    # Plan mode (enter + exit). Both are main-thread tools; the subagent filter
    # in get_tools() drops the main-thread-only ones for sub-agents.
    EnterPlanModeTool = _wrap_module_tool(
        "hare.tools_impl.EnterPlanModeTool.enter_plan_mode_tool", "EnterPlanMode"
    )
    ExitPlanModeTool = _wrap_module_tool(
        "hare.tools_impl.ExitPlanModeTool.exit_plan_mode_tool", "ExitPlanMode"
    )
    tools.extend([EnterPlanModeTool, ExitPlanModeTool])

    # Main-thread interaction / task / messaging tools — unconditional in the
    # 2.1.88 reference base set (getAllBaseTools). Implementations live under
    # hare/tools_impl/; they were previously implemented but never registered.
    AskUserQuestionTool = _wrap_module_tool(
        "hare.tools_impl.AskUserQuestionTool.ask_user_question_tool", "AskUserQuestion"
    )
    TaskOutputTool = _wrap_module_tool(
        "hare.tools_impl.TaskOutputTool.task_output_tool", "TaskOutput", read_only=True
    )
    TaskStopTool = _wrap_module_tool(
        "hare.tools_impl.TaskStopTool.task_stop_tool", "TaskStop"
    )
    # SendMessage is a class-based tool (singleton), unconditional in the 2.1.88
    # base set. It was previously mis-registered via _wrap_module_tool (which is
    # for function-modules), so it never worked; import the singleton directly.
    tools.extend([AskUserQuestionTool, TaskOutputTool, TaskStopTool, SendMessageTool])

    return tools


def _get_deny_rule_for_tool(
    permission_context: ToolPermissionContext, tool: Any
) -> bool:
    """Check if a tool is blanket-denied by the permission context.

    Matching TS filterToolsByDenyRules: checks both the tool name AND
    MCP server prefix (e.g. "mcp__github" denies all tools from github server).
    """
    deny_rules = permission_context.always_deny_rules
    for _source, rules in deny_rules.items():
        for rule in rules:
            if tool_matches_name(tool, rule):
                return True
            # TS: also check MCP server-level deny (prefix match)
            mcp_info = getattr(tool, "mcp_info", None)
            if mcp_info is not None:
                server_name = getattr(mcp_info, "server_name", None)
                if server_name and rule == f"mcp__{server_name}":
                    return True
    return False


def filter_tools_by_deny_rules(
    tools: Sequence[Tool], permission_context: ToolPermissionContext
) -> list[Tool]:
    """
    Filters out tools that are blanket-denied by the permission context.
    A tool is filtered out if there's a deny rule matching its name with no
    ruleContent (i.e., a blanket deny for that tool).
    """
    return [t for t in tools if not _get_deny_rule_for_tool(permission_context, t)]


def get_tools(permission_context: ToolPermissionContext) -> list[Tool]:
    """
    Get tools filtered for the given permission context.

    Simple mode (CLAUDE_CODE_SIMPLE): only Bash, Read, and Edit tools.
    """
    if is_env_truthy(os.environ.get("CLAUDE_CODE_SIMPLE")):
        from hare.tools_impl.BashTool.bash_tool import BashTool

        FileReadTool = _wrap_module_tool(
            "hare.tools_impl.FileReadTool.file_read_tool", "Read", read_only=True
        )
        FileEditTool = _wrap_module_tool(
            "hare.tools_impl.FileEditTool.file_edit_tool", "Edit"
        )
        return filter_tools_by_deny_rules(
            [BashTool, FileReadTool, FileEditTool], permission_context
        )

    all_tools = get_all_base_tools()
    allowed = filter_tools_by_deny_rules(all_tools, permission_context)
    return [t for t in allowed if t.is_enabled()]


def assemble_tool_pool(
    permission_context: ToolPermissionContext,
    mcp_tools: list[Tool] | None = None,
) -> list[Tool]:
    """
    Assemble the full tool pool for a given permission context and MCP tools.

    This is the single source of truth for combining built-in tools with MCP tools.
    """
    built_in = get_tools(permission_context)
    if not mcp_tools:
        return sorted(built_in, key=lambda t: t.name)

    allowed_mcp = filter_tools_by_deny_rules(mcp_tools, permission_context)
    # Built-in tools take precedence over MCP tools by name
    built_in_names = {t.name for t in built_in}
    deduped_mcp = [t for t in allowed_mcp if t.name not in built_in_names]

    return sorted(built_in, key=lambda t: t.name) + sorted(
        deduped_mcp, key=lambda t: t.name
    )


def get_merged_tools(
    permission_context: ToolPermissionContext,
    mcp_tools: list[Tool] | None = None,
) -> list[Tool]:
    """Get all tools including both built-in tools and MCP tools."""
    built_in = get_tools(permission_context)
    if not mcp_tools:
        return built_in
    return [*built_in, *mcp_tools]
