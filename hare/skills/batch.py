"""
Batch skill execution — run multiple skills with progress tracking.

Port of: src/skills/bundled/batch.ts (124 lines)
"""

from __future__ import annotations

from typing import Any


async def execute_skill_batch(
    skills: list[dict[str, Any]],
    context: Any = None,
    max_concurrent: int = 3,
) -> list[dict[str, Any]]:
    """Execute a batch of skills, respecting dependencies and concurrency limits.

    Each skill gets executed in order. Skills with dependencies wait for
    their prerequisites to complete before starting.
    """
    results: list[dict[str, Any]] = []
    running: dict[str, Any] = {}
    completed: set[str] = set()
    failed: set[str] = set()

    import asyncio

    async def _run_one(skill: dict[str, Any]) -> dict[str, Any]:
        name = skill.get("name", "unknown")
        deps = skill.get("depends_on", [])
        executor = skill.get("_executor")

        # Wait for dependencies
        for dep in deps:
            while dep not in completed and dep not in failed:
                await asyncio.sleep(0.1)
            if dep in failed:
                return {
                    "name": name,
                    "status": "skipped",
                    "reason": f"dependency '{dep}' failed",
                }

        try:
            if executor and callable(executor):
                result = (
                    await executor(skill, context) if context else await executor(skill)
                )
                completed.add(name)
                return {"name": name, "status": "completed", "result": result}
            else:
                completed.add(name)
                return {"name": name, "status": "completed"}
        except Exception as e:
            failed.add(name)
            return {"name": name, "status": "failed", "error": str(e)}

    # Execute in order, respecting concurrency
    pending = list(skills)
    tasks: list[asyncio.Task[Any]] = []

    while pending or tasks:
        # Start new tasks up to concurrency limit
        while pending and len(tasks) < max_concurrent:
            skill = pending.pop(0)
            tasks.append(asyncio.ensure_future(_run_one(skill)))

        # Wait for at least one to complete
        if tasks:
            done, tasks = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for t in done:
                results.append(t.result())

    # Sort results to match input order
    name_order = {s.get("name", ""): i for i, s in enumerate(skills)}
    results.sort(key=lambda r: name_order.get(r.get("name", ""), 999))

    return results
