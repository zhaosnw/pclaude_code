"""
AgentTool – delegate work to a sub-agent.

Port of: src/tools/AgentTool/AgentTool.tsx

Launches a new agent with its own context and tool set to handle
complex, multi-step tasks autonomously.
"""

from __future__ import annotations

from typing import Any, Optional
from uuid import uuid4

from hare.app_types.ids import AgentId
from hare.tool import ToolBase, ToolResult, ToolUseContext
from hare.app_types.permissions import (
    EXTERNAL_PERMISSION_MODES,
    PermissionAllowDecision,
    PermissionResult,
)
from hare.utils.agent_swarms_enabled import is_agent_swarms_enabled
from hare.utils.bundle_feature import feature

AGENT_TOOL_NAME = "Agent"
LEGACY_AGENT_TOOL_NAME = "Task"


class _AgentTool(ToolBase):
    name = AGENT_TOOL_NAME
    # AgentTool.tsx:228 — the alias is the legacy name verbatim. hare lowercased
    # it, and tool lookup is exact (Tool.ts:352), so a model emitting the
    # documented "Task" name found no tool at all: the turn fell through to the
    # "tool not found" path and the agent was never dispatched.
    aliases = [LEGACY_AGENT_TOOL_NAME]
    search_hint = "delegate work to a subagent"
    max_result_size_chars = 100_000

    def input_schema(self) -> dict[str, Any]:
        import os

        # isolation: external builds offer only "worktree"; ant adds "remote".
        isolation_modes = (
            ["worktree", "remote"]
            if os.environ.get("USER_TYPE") == "ant"
            else ["worktree"]
        )
        # 2.1.88 ALWAYS advertises the multi-agent params (name/team_name/mode)
        # and isolation; the swarm feature is enforced at call time, not by
        # hiding the schema. cwd is the only KAIROS/ant-gated field.
        properties: dict[str, Any] = {
            "prompt": {
                "type": "string",
                "description": "The task for the agent to perform.",
            },
            "description": {
                "type": "string",
                "description": "A short (3-5 word) description of the task.",
            },
            # TS advertises subagent_type as z.string() (no enum) and resolves
            # it at call time against the active agent definitions (built-in +
            # user/project/MCP), raising for unknown types. hare matches the
            # free-string SCHEMA; note its simplified call() does not yet resolve
            # or validate the type (it runs a generic subagent) — a documented
            # limitation, not full call-time resolution.
            "subagent_type": {
                "type": "string",
                "description": "The type of specialized agent to use for this task",
            },
            "model": {
                "type": "string",
                "enum": ["sonnet", "opus", "haiku"],
                "description": "Optional model override for this agent. Takes "
                "precedence over the agent definition's model frontmatter. If "
                "omitted, uses the agent definition's model, or inherits from "
                "the parent.",
            },
            "run_in_background": {
                "type": "boolean",
                "description": "Set to true to run this agent in the background. "
                "You will be notified when it completes.",
            },
            "name": {
                "type": "string",
                "description": "Name for the spawned agent. Makes it addressable "
                "via SendMessage({to: name}) while running.",
            },
            "team_name": {
                "type": "string",
                "description": "Team name for spawning. Uses current team context "
                "if omitted.",
            },
            "mode": {
                "type": "string",
                # TS permissionModeSchema = z.enum(PERMISSION_MODES); 'auto' is
                # only added under feature('TRANSCRIPT_CLASSIFIER').
                "enum": list(EXTERNAL_PERMISSION_MODES)
                + (["auto"] if feature("TRANSCRIPT_CLASSIFIER") else []),
                "description": "Permission mode for spawned teammate (e.g., "
                '"plan" to require plan approval).',
            },
            "isolation": {
                "type": "string",
                "enum": isolation_modes,
                "description": 'Isolation mode. "worktree" creates a temporary '
                "git worktree so the agent works on an isolated copy of the repo.",
            },
        }
        # cwd: only when KAIROS/ant (matches 2.1.88's .omit({cwd}) for external).
        if feature("KAIROS"):
            properties["cwd"] = {
                "type": "string",
                "description": "Absolute path to run the agent in. Overrides the "
                "working directory for all filesystem and shell operations "
                'within this agent. Mutually exclusive with isolation: "worktree".',
            }
        return {
            "type": "object",
            "properties": properties,
            "required": ["description", "prompt"],
        }

    def is_read_only(self, input: dict[str, Any]) -> bool:
        return True  # Delegates permission checks to its underlying tools

    def is_concurrency_safe(self, input: dict[str, Any]) -> bool:
        return True

    async def check_permissions(
        self, input: dict[str, Any], context: ToolUseContext
    ) -> PermissionResult:
        return PermissionAllowDecision(behavior="allow", updated_input=input)

    async def prompt(self, options: dict[str, Any]) -> str:
        return (
            "Launch a new agent to handle complex, multi-step tasks autonomously. "
            "Each agent has its own context and can use tools to complete the task."
        )

    async def description(self, input: dict[str, Any], options: dict[str, Any]) -> str:
        desc = input.get("description", "")
        return desc if desc else "Launch sub-agent"

    def user_facing_name(self, input: Optional[dict[str, Any]] = None) -> str:
        return AGENT_TOOL_NAME

    def to_auto_classifier_input(self, input: dict[str, Any]) -> Any:
        subagent_type = input.get("subagent_type", "")
        mode = input.get("mode", "")
        tags = [t for t in [subagent_type, f"mode={mode}" if mode else None] if t]
        prefix = f"({', '.join(tags)}): " if tags else ": "
        return f"{prefix}{input.get('prompt', '')}"

    async def call(
        self,
        args: dict[str, Any],
        context: ToolUseContext,
        can_use_tool: Any = None,
        parent_message: Any = None,
        on_progress: Any = None,
    ) -> ToolResult:
        """
        Launch a sub-agent to handle a task.

        In the full TS implementation, this creates a child QueryEngine with its
        own tool set and runs it to completion. For this port, we implement a
        simplified version that runs the query through a new engine instance.
        """
        prompt = args.get("prompt", "")
        description = args.get("description", "")
        subagent_type = args.get("subagent_type", "generalPurpose")
        model = args.get("model")
        run_in_background = args.get("run_in_background", False)
        team_name = args.get("team_name")

        # Agent-teams access guard (TS AgentTool: team_name without
        # isAgentSwarmsEnabled() THROWS). Raising — not returning — is what makes
        # the tool_execution pipeline emit an is_error tool_result, matching TS;
        # a returned ToolResult would be delivered as a successful tool output.
        if team_name and not is_agent_swarms_enabled():
            raise ValueError("Agent Teams is not yet available on your plan.")

        if not prompt:
            return ToolResult(data="Error: prompt is required")

        try:
            from hare.query_engine import QueryEngine, QueryEngineConfig
            from hare.tools import get_tools
            from hare.commands import get_commands
            from hare.tool import get_empty_tool_permission_context
            from hare.utils.cwd import get_cwd
            from hare.tools_impl.AgentTool.agent_tool_utils import (
                get_agent_model,
                resolve_agent_tools,
            )
            from hare.tools_impl.AgentTool.built_in_agents import find_builtin_agent

            permission_context = get_empty_tool_permission_context()
            tools = get_tools(permission_context)
            commands = await get_commands(get_cwd())

            # Resolve subagent_type to its built-in agent definition so the child
            # gets that agent's DEDICATED system prompt + restricted tool set —
            # not the full main-loop prompt/toolset. Unknown types fall back to
            # general-purpose (TS raises; hare degrades gracefully).
            agent_def = find_builtin_agent(subagent_type) or find_builtin_agent(
                "generalPurpose"
            )
            child_tools = (
                resolve_agent_tools(agent_def, tools) if agent_def else tools
            )
            resolved_model = (
                get_agent_model(agent_def, requested_model=model or "")
                if agent_def
                else model
            )
            # Dedicated subagent system prompt (general-purpose: "You are an agent
            # for Claude Code ... concise report"). None falls back to the engine's
            # default assembly only if no definition prompt exists.
            agent_system_prompt = agent_def.custom_system_prompt if agent_def else ""

            async def child_can_use_tool(
                tool: Any, inp: Any, ctx: Any, msg: Any, tool_use_id: str, force: Any
            ) -> Any:
                return PermissionAllowDecision(behavior="allow", updated_input=inp)

            child_engine = QueryEngine(
                QueryEngineConfig(
                    cwd=get_cwd(),
                    tools=child_tools,
                    commands=commands,
                    can_use_tool=child_can_use_tool,
                    get_app_state=lambda: {},
                    set_app_state=lambda f: None,
                    user_specified_model=resolved_model or None,
                    verbose=False,
                    # Identifies the child as a subagent, so its teardown fires
                    # SubagentStop rather than a main-session Stop.
                    agent_id=AgentId(str(uuid4())),
                    agent_type=subagent_type or "general-purpose",
                )
            )

            submit_kwargs: dict[str, Any] = {}
            if agent_system_prompt:
                submit_kwargs["system_prompt_override"] = [agent_system_prompt]

            # Async dispatch (AgentTool.tsx:1328): run_in_background runs the
            # subagent as a background task and returns an "Async agent launched"
            # result immediately, rather than blocking the parent until the
            # subagent finishes. The parent is expected to end its turn; the
            # subagent runs on independently. Synchronous dispatch
            # (run_in_background=false) keeps the original blocking behavior.
            if run_in_background:
                agent_id = str(uuid4())

                async def _drain() -> None:
                    try:
                        async for _ in child_engine.submit_message(
                            prompt, **submit_kwargs
                        ):
                            pass
                    except Exception:  # noqa: BLE001 - background, must not raise
                        pass

                import asyncio

                asyncio.ensure_future(_drain())
                launched = (
                    "Async agent launched successfully.\n"
                    f"agentId: {agent_id} (internal ID - do not mention to user. "
                    f"Use SendMessage with to: '{agent_id}' to continue this agent.)\n"
                    "The agent is working in the background. You will be notified "
                    "automatically when it completes.\n"
                    "Briefly tell the user what you launched and end your response. "
                    "Do not generate any other text — agent results will arrive in "
                    "a subsequent message."
                )
                return ToolResult(data=launched)

            result_text = ""
            async for msg in child_engine.submit_message(prompt, **submit_kwargs):
                msg_type = msg.get("type", "")
                if msg_type == "result":
                    result_text = msg.get("result", "")

            if result_text:
                return ToolResult(data=result_text)
            else:
                return ToolResult(data="Agent completed the task.")

        except Exception as e:
            return ToolResult(data=f"Error launching agent: {e}")


AgentTool = _AgentTool()
