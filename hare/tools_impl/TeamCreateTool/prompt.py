"""
TeamCreate tool prompt.

Port of: src/tools/TeamCreateTool/prompt.ts
"""


def get_prompt() -> str:
    return """# TeamCreate

## When to Use

Use this tool proactively whenever:
- The user explicitly asks to use a team, swarm, or group of agents
- The user mentions wanting agents to work together, coordinate, or collaborate
- A task is complex enough that it would benefit from parallel work by multiple agents

When in doubt about whether a task warrants a team, prefer spawning a team.

## Choosing Agent Types for Teammates

When spawning teammates via the Agent tool, choose the `subagent_type` based on what tools the agent needs for its task.

- **Read-only agents** (e.g., Explore, Plan) cannot edit or write files. Only assign them research, search, or planning tasks.
- **Full-capability agents** (e.g., general-purpose) have access to all tools including file editing, writing, and bash.
- **Custom agents** defined in `.hare/agents/` may have their own tool restrictions.

Create a new team to coordinate multiple agents working on a project.

## Team Workflow

1. **Create a team** with TeamCreate
2. **Create tasks** using the Task tools
3. **Spawn teammates** using the Agent tool with `team_name` and `name` parameters
4. **Assign tasks** using TaskUpdate with `owner`
5. **Teammates work on assigned tasks** and mark them completed
6. **Shutdown your team** when the task is completed

## Task Ownership

Tasks are assigned using TaskUpdate with the `owner` parameter.

## Automatic Message Delivery

Messages from teammates are automatically delivered to you. You do NOT need to manually check your inbox."""
