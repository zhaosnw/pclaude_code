"""Port of: src/tools/AgentTool/prompt.ts"""

from __future__ import annotations

import os
from typing import Optional

from ..FileReadTool.prompt import FILE_READ_TOOL_NAME
from ..FileWriteTool.prompt import FILE_WRITE_TOOL_NAME
from ..GlobTool.prompt import GLOB_TOOL_NAME
from ..SendMessageTool.prompt import SEND_MESSAGE_TOOL_NAME

AGENT_TOOL_NAME = "Agent"


def _get_tools_description(agent: dict) -> str:
    tools = agent.get("tools") or []
    disallowed_tools = agent.get("disallowedTools") or []
    has_allowlist = len(tools) > 0
    has_denylist = len(disallowed_tools) > 0

    if has_allowlist and has_denylist:
        deny_set = set(disallowed_tools)
        effective_tools = [t for t in tools if t not in deny_set]
        if not effective_tools:
            return "None"
        return ", ".join(effective_tools)
    elif has_allowlist:
        return ", ".join(tools)
    elif has_denylist:
        return f"All tools except {', '.join(disallowed_tools)}"
    return "All tools"


def format_agent_line(agent: dict) -> str:
    """Format one agent line: ``- type: whenToUse (Tools: ...)``."""
    tools_description = _get_tools_description(agent)
    return f"- {agent['agentType']}: {agent['whenToUse']} (Tools: {tools_description})"


def should_inject_agent_list_in_messages() -> bool:
    env_val = os.environ.get("CLAUDE_CODE_AGENT_LIST_IN_MESSAGES", "")
    if env_val.lower() in ("1", "true", "yes"):
        return True
    if env_val.lower() in ("0", "false", "no"):
        return False
    return False


def get_prompt(
    agent_definitions: list[dict],
    *,
    is_coordinator: bool = False,
    allowed_agent_types: Optional[list[str]] = None,
    fork_enabled: bool = False,
    subscription_type: str = "free",
    has_embedded_search_tools: bool = False,
    is_teammate: bool = False,
    is_in_process_teammate: bool = False,
    disable_background_tasks: bool = False,
    user_type: Optional[str] = None,
) -> str:
    effective_agents = (
        [a for a in agent_definitions if a["agentType"] in allowed_agent_types]
        if allowed_agent_types
        else agent_definitions
    )

    when_to_fork_section = ""
    if fork_enabled:
        when_to_fork_section = """

## When to fork

Fork yourself (omit `subagent_type`) when the intermediate tool output isn't worth keeping in your context. The criterion is qualitative \u2014 "will I need this output again" \u2014 not task size.
- **Research**: fork open-ended questions. If research can be broken into independent questions, launch parallel forks in one message. A fork beats a fresh subagent for this \u2014 it inherits context and shares your cache.
- **Implementation**: prefer to fork implementation work that requires more than a couple of edits. Do research before jumping to implementation.

Forks are cheap because they share your prompt cache. Don't set `model` on a fork \u2014 a different model can't reuse the parent's cache. Pass a short `name` (one or two words, lowercase) so the user can see the fork in the teams panel and steer it mid-run.

**Don't peek.** The tool result includes an `output_file` path \u2014 do not Read or tail it unless the user explicitly asks for a progress check. You get a completion notification; trust it. Reading the transcript mid-flight pulls the fork's tool noise into your context, which defeats the point of forking.

**Don't race.** After launching, you know nothing about what the fork found. Never fabricate or predict fork results in any format \u2014 not as prose, summary, or structured output. The notification arrives as a user-role message in a later turn; it is never something you write yourself. If the user asks a follow-up before the notification lands, tell them the fork is still running \u2014 give status, not a guess.

**Writing a fork prompt.** Since the fork inherits your context, the prompt is a *directive* \u2014 what to do, not what the situation is. Be specific about scope: what's in, what's out, what another agent is handling. Don't re-explain background.
"""

    when_spawning_prefix = (
        "When spawning a fresh agent (with a `subagent_type`), it starts with zero context. "
        if fork_enabled
        else ""
    )
    terse_prefix = "For fresh agents, terse" if fork_enabled else "Terse"

    writing_the_prompt_section = f"""

## Writing the prompt

{when_spawning_prefix}Brief the agent like a smart colleague who just walked into the room \u2014 it hasn't seen this conversation, doesn't know what you've tried, doesn't understand why this task matters.
- Explain what you're trying to accomplish and why.
- Describe what you've already learned or ruled out.
- Give enough context about the surrounding problem that the agent can make judgment calls rather than just following a narrow instruction.
- If you need a short response, say so ("report in under 200 words").
- Lookups: hand over the exact command. Investigations: hand over the question \u2014 prescribed steps become dead weight when the premise is wrong.

{terse_prefix} command-style prompts produce shallow, generic work.

**Never delegate understanding.** Don't write "based on your findings, fix the bug" or "based on the research, implement it." Those phrases push synthesis onto the agent instead of doing it yourself. Write prompts that prove you understood: include file paths, line numbers, what specifically to change.
"""

    fork_examples = f"""Example usage:

<example>
user: "What's left on this branch before we can ship?"
assistant: <thinking>Forking this \u2014 it's a survey question. I want the punch list, not the git output in my context.</thinking>
{AGENT_TOOL_NAME}({{
  name: "ship-audit",
  description: "Branch ship-readiness audit",
  prompt: "Audit what's left before this branch can ship. Check: uncommitted changes, commits ahead of main, whether tests exist, whether the GrowthBook gate is wired up, whether CI-relevant files changed. Report a punch list \u2014 done vs. missing. Under 200 words."
}})
assistant: Ship-readiness audit running.
<commentary>
Turn ends here. The coordinator knows nothing about the findings yet. What follows is a SEPARATE turn \u2014 the notification arrives from outside, as a user-role message. It is not something the coordinator writes.
</commentary>
[later turn \u2014 notification arrives as user message]
assistant: Audit's back. Three blockers: no tests for the new prompt path, GrowthBook gate wired but not in build_flags.yaml, and one uncommitted file.
</example>

<example>
user: "so is the gate wired up or not"
<commentary>
User asks mid-wait. The audit fork was launched to answer exactly this, and it hasn't returned. The coordinator does not have this answer. Give status, not a fabricated result.
</commentary>
assistant: Still waiting on the audit \u2014 that's one of the things it's checking. Should land shortly.
</example>

<example>
user: "Can you get a second opinion on whether this migration is safe?"
assistant: <thinking>I'll ask the code-reviewer agent \u2014 it won't see my analysis, so it can give an independent read.</thinking>
<commentary>
A subagent_type is specified, so the agent starts fresh. It needs full context in the prompt. The briefing explains what to assess and why.
</commentary>
{AGENT_TOOL_NAME}({{
  name: "migration-review",
  description: "Independent migration review",
  subagent_type: "code-reviewer",
  prompt: "Review migration 0042_user_schema.sql for safety. Context: we're adding a NOT NULL column to a 50M-row table. Existing rows get a backfill default. I want a second opinion on whether the backfill approach is safe under concurrent writes \u2014 I've checked locking behavior but want independent verification. Report: is this safe, and if not, what specifically breaks?"
}})
</example>
"""

    current_examples = f"""Example usage:

<example_agent_descriptions>
"test-runner": use this agent after you are done writing code to run tests
"greeting-responder": use this agent to respond to user greetings with a friendly joke
</example_agent_descriptions>

<example>
user: "Please write a function that checks if a number is prime"
assistant: I'm going to use the {FILE_WRITE_TOOL_NAME} tool to write the following code:
<code>
function isPrime(n) {{
  if (n <= 1) return false
  for (let i = 2; i * i <= n; i++) {{
    if (n % i === 0) return false
  }}
  return true
}}
</code>
<commentary>
Since a significant piece of code was written and the task was completed, now use the test-runner agent to run the tests
</commentary>
assistant: Uses the {AGENT_TOOL_NAME} tool to launch the test-runner agent
</example>

<example>
user: "Hello"
<commentary>
Since the user is greeting, use the greeting-responder agent to respond with a friendly joke
</commentary>
assistant: "I'm going to use the {AGENT_TOOL_NAME} tool to launch the greeting-responder agent"
</example>
"""

    list_via_attachment = should_inject_agent_list_in_messages()

    if list_via_attachment:
        agent_list_section = "Available agent types are listed in <system-reminder> messages in the conversation."
    else:
        lines = "\n".join(format_agent_line(a) for a in effective_agents)
        agent_list_section = (
            f"Available agent types and the tools they have access to:\n{lines}"
        )

    if fork_enabled:
        type_note = (
            f"When using the {AGENT_TOOL_NAME} tool, specify a subagent_type to use a "
            f"specialized agent, or omit it to fork yourself \u2014 a fork inherits your full conversation context."
        )
    else:
        type_note = (
            f"When using the {AGENT_TOOL_NAME} tool, specify a subagent_type parameter to select "
            f"which agent type to use. If omitted, the general-purpose agent is used."
        )

    shared = f"""Launch a new agent to handle complex, multi-step tasks autonomously.

The {AGENT_TOOL_NAME} tool launches specialized agents (subprocesses) that autonomously handle complex tasks. Each agent type has specific capabilities and tools available to it.

{agent_list_section}

{type_note}"""

    if is_coordinator:
        return shared

    if has_embedded_search_tools:
        file_search_hint = "`find` via the Bash tool"
        content_search_hint = "`grep` via the Bash tool"
    else:
        file_search_hint = f"the {GLOB_TOOL_NAME} tool"
        content_search_hint = f"the {GLOB_TOOL_NAME} tool"

    when_not_to_use_section = ""
    if not fork_enabled:
        when_not_to_use_section = f"""
When NOT to use the {AGENT_TOOL_NAME} tool:
- If you want to read a specific file path, use the {FILE_READ_TOOL_NAME} tool or {file_search_hint} instead of the {AGENT_TOOL_NAME} tool, to find the match more quickly
- If you are searching for a specific class definition like "class Foo", use {content_search_hint} instead, to find the match more quickly
- If you are searching for code within a specific file or set of 2-3 files, use the {FILE_READ_TOOL_NAME} tool instead of the {AGENT_TOOL_NAME} tool, to find the match more quickly
- Other tasks that are not related to the agent descriptions above
"""

    concurrency_note = ""
    if not list_via_attachment and subscription_type != "pro":
        concurrency_note = (
            "\n- Launch multiple agents concurrently whenever possible, to maximize performance; "
            "to do that, use a single message with multiple tool uses"
        )

    background_note = ""
    if not disable_background_tasks and not is_in_process_teammate and not fork_enabled:
        background_note = (
            "\n- You can optionally run agents in the background using the run_in_background parameter. "
            "When an agent runs in the background, you will be automatically notified when it completes "
            "\u2014 do NOT sleep, poll, or proactively check on its progress. Continue with other work "
            "or respond to the user instead."
            "\n- **Foreground vs background**: Use foreground (default) when you need the agent's results "
            "before you can proceed \u2014 e.g., research agents whose findings inform your next steps. "
            "Use background when you have genuinely independent work to do in parallel."
        )

    continue_note = (
        "Each fresh Agent invocation with a subagent_type starts without context \u2014 provide a complete task description."
        if fork_enabled
        else "Each Agent invocation starts fresh \u2014 provide a complete task description."
    )

    context_note = (
        "" if fork_enabled else ", since it is not aware of the user's intent"
    )

    remote_section = ""
    if user_type == "ant":
        remote_section = (
            '\n- You can set `isolation: "remote"` to run the agent in a remote CCR environment. '
            "This is always a background task; you'll be notified when it completes. "
            "Use for long-running tasks that need a fresh sandbox."
        )

    teammate_section = ""
    if is_in_process_teammate:
        teammate_section = (
            "\n- The run_in_background, name, team_name, and mode parameters are not available "
            "in this context. Only synchronous subagents are supported."
        )
    elif is_teammate:
        teammate_section = (
            "\n- The name, team_name, and mode parameters are not available in this context "
            "\u2014 teammates cannot spawn other teammates. Omit them to spawn a subagent."
        )

    examples = fork_examples if fork_enabled else current_examples

    return f"""{shared}
{when_not_to_use_section}

Usage notes:
- Always include a short description (3-5 words) summarizing what the agent will do{concurrency_note}
- When the agent is done, it will return a single message back to you. The result returned by the agent is not visible to the user. To show the user the result, you should send a text message back to the user with a concise summary of the result.{background_note}
- To continue a previously spawned agent, use {SEND_MESSAGE_TOOL_NAME} with the agent's ID or name as the `to` field. The agent resumes with its full context preserved. {continue_note}
- The agent's outputs should generally be trusted
- Clearly tell the agent whether you expect it to write code or just to do research (search, file reads, web fetches, etc.){context_note}
- If the agent description mentions that it should be used proactively, then you should try your best to use it without the user having to ask for it first. Use your judgement.
- If the user specifies that they want you to run agents "in parallel", you MUST send a single message with multiple {AGENT_TOOL_NAME} tool use content blocks. For example, if you need to launch both a build-validator agent and a test-runner agent in parallel, send a single message with both tool calls.
- You can optionally set `isolation: "worktree"` to run the agent in a temporary git worktree, giving it an isolated copy of the repository. The worktree is automatically cleaned up if the agent makes no changes; if changes are made, the worktree path and branch are returned in the result.{remote_section}{teammate_section}{when_to_fork_section}{writing_the_prompt_section}

{examples}"""
