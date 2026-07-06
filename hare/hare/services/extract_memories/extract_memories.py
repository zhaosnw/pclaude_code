"""
Extract memories from conversation via forked background agent.

Port of: src/services/extractMemories/extractMemories.ts

Key design (matching TS):
- Mutex: if main agent already wrote to memory dir, skip extraction
- Throttle: turnsSinceLastExtraction counter; only fires every N turns
- Tool whitelist: createAutoMemCanUseTool restricts permissions:
  - Read/Grep/Glob: unrestricted (need to understand context)
  - Bash: read-only commands only (ls, find, grep, etc.)
  - Edit/Write: only within auto-memory directory
  - All other tools: denied
- Trailing extraction: stashed context for concurrent handling
- Cache sharing: forked agent shares system prompt + tools for cache hits
- Event logging: mirrors TS logEvent calls for telemetry (tengu_extract_memories_*)
- initExtractMemories() closure-scoped state pattern matching TS exactly
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Callable


# ---------------------------------------------------------------------------
# Message visibility helpers (TS L74-110)
# ---------------------------------------------------------------------------


def is_model_visible_message(message: Any) -> bool:
    """Return True if this message is visible to the model (sent in API calls).

    TS isModelVisibleMessage: excludes progress, system, and attachment messages.
    """
    if isinstance(message, dict):
        msg_type = message.get("type", "")
        return msg_type in ("user", "assistant")
    msg_type = getattr(message, "type", "")
    return msg_type in ("user", "assistant")


def count_model_visible_messages_since(
    messages: list[Any],
    since_uuid: str | None,
) -> int:
    """Count model-visible messages after the cursor UUID.

    TS countModelVisibleMessagesSince (L82-110):
    - If sinceUuid is None/empty, count ALL model-visible messages.
    - Scan forward from the first message whose uuid == sinceUuid.
    - If sinceUuid is NOT found (e.g., removed by context compaction),
      fall back to counting ALL model-visible messages rather than
      returning 0 — which would permanently disable extraction.
    """
    if not since_uuid:
        return sum(1 for m in messages if is_model_visible_message(m))

    found_start = False
    n = 0
    for message in messages:
        mid = (
            message.get("uuid")
            if isinstance(message, dict)
            else getattr(message, "uuid", None)
        )
        if not found_start:
            if mid == since_uuid:
                found_start = True
            continue
        if is_model_visible_message(message):
            n += 1

    # Fallback: if sinceUuid was not found (context compaction removed it),
    # count ALL model-visible messages to avoid permanent extraction stall.
    if not found_start:
        return sum(1 for m in messages if is_model_visible_message(m))
    return n


# ---------------------------------------------------------------------------
# Write detection: extract file paths from tool_use blocks (TS L228-269)
# ---------------------------------------------------------------------------


def get_written_file_path(block: Any) -> str | None:
    """Extract file_path from a tool_use block's input, if it is a Write/Edit.

    TS getWrittenFilePath (L232-249): returns undefined when the block is
    not an Edit/Write tool use or has no file_path.
    """
    if isinstance(block, dict):
        block_type = block.get("type", "")
        tool_name = block.get("name", "")
        tool_input = block.get("input", {})
    else:
        block_type = getattr(block, "type", "")
        tool_name = getattr(block, "name", "")
        tool_input = getattr(block, "input", {})

    if block_type != "tool_use":
        return None
    if tool_name not in ("Write", "Edit"):
        return None

    if isinstance(tool_input, dict):
        fp = tool_input.get("file_path")
        if isinstance(fp, str):
            return fp
    elif hasattr(tool_input, "file_path"):
        fp = getattr(tool_input, "file_path")
        if isinstance(fp, str):
            return fp
    return None


def extract_written_paths(agent_messages: list[Any]) -> list[str]:
    """Extract all written file paths from agent output, deduplicated.

    TS extractWrittenPaths (L251-269): scans assistant messages for
    Write/Edit tool_use blocks, collects file_path values, returns
    unique paths.
    """
    paths: list[str] = []
    for message in agent_messages:
        msg_type = (
            message.get("type")
            if isinstance(message, dict)
            else getattr(message, "type", None)
        )
        if msg_type != "assistant":
            continue

        content = (
            message.get("message", {}).get("content", [])
            if isinstance(message, dict)
            else getattr(getattr(message, "message", None), "content", [])
        )
        if not isinstance(content, list):
            continue

        for block in content:
            fp = get_written_file_path(block)
            if fp is not None and fp not in paths:
                paths.append(fp)

    return paths


# ---------------------------------------------------------------------------
# Closure-scoped state (TS initExtractMemories closure)
# ---------------------------------------------------------------------------

_extractor: Callable[..., Any] | None = None
_drainer: Callable[..., Any] = lambda timeout_ms: asyncio.sleep(0)
_in_flight_extractions: set[asyncio.Task[Any]] = set()


def _get_throttle_threshold() -> int:
    """TS: tengu_bramble_lintel GrowthBook flag, defaults to 1."""
    try:
        return int(os.environ.get("HARE_MEMORY_EXTRACT_THROTTLE", "1"))
    except (ValueError, TypeError):
        return 1


def _reset_extraction_state() -> None:
    """Reset closure state (for testing)."""
    global _extractor, _drainer, _in_flight_extractions
    _extractor = None
    _drainer = lambda timeout_ms: asyncio.sleep(0)
    _in_flight_extractions = set()


# ---------------------------------------------------------------------------
# Mutex: check if main agent already wrote to memory dir (TS hasMemoryWritesSince)
# ---------------------------------------------------------------------------


def _has_memory_writes_since(
    messages: list[dict[str, Any]],
    memory_dir: str,
    since_uuid: str | None,
) -> bool:
    """Check if any Write/Edit tool invocations in messages targeted the memory dir.

    TS hasMemoryWritesSince (L121-148): scans assistant messages after the
    cursor UUID for tool calls that wrote to paths within the auto-memory
    directory. If found, skip extraction.
    """
    if not memory_dir:
        return False

    try:
        from hare.memdir.paths import is_auto_mem_path
    except ImportError:
        return False

    found_start = since_uuid is None
    for msg in messages:
        if not found_start:
            mid = msg.get("uuid", "")
            if mid == since_uuid:
                found_start = True
            continue
        if msg.get("type") != "assistant":
            continue
        content = msg.get("message", {}).get("content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            fp = get_written_file_path(block)
            if fp is not None and is_auto_mem_path(fp):
                return True
    return False


# ---------------------------------------------------------------------------
# Memory saved message creation (TS createMemorySavedMessage in messages.ts)
# ---------------------------------------------------------------------------


def _create_memory_saved_message(
    memory_paths: list[str],
    team_count: int = 0,
) -> dict[str, Any]:
    """Create a system notification that memories were saved.

    TS: createMemorySavedMessage — returns a SystemMessage with
    memory paths and optional team count.
    """
    return {
        "type": "system",
        "subtype": "init",
        "uuid": "",
        "session_id": "",
        "message": {
            "content": f"Memory saved: {len(memory_paths)} file(s)",
            "memory_paths": list(memory_paths),
            "team_count": team_count,
        },
        "isMeta": True,
    }


# ---------------------------------------------------------------------------
# Tool permission whitelist (TS createAutoMemCanUseTool L171-222)
# ---------------------------------------------------------------------------


def _create_auto_mem_can_use_tool(
    memory_dir: str,
) -> Callable[..., Any]:
    """Create a canUseTool callback that restricts forked agent permissions.

    TS createAutoMemCanUseTool whitelist (L178-213):
    - Read/Grep/Glob/WebSearch/WebFetch: allow (read-only)
    - Bash: only read-only commands (via command keyword check)
    - Edit/Write: only if path is within memory directory
    - ListMcpResources/ReadMcpResource: allow (read-only MCP)
    - All others: deny

    IMPORTANT (TS L178): same tool list, different canUseTool callback —
    giving the fork a different tool list would break prompt cache sharing.
    """
    safe_tools = {
        "Read",
        "Grep",
        "Glob",
        "WebSearch",
        "WebFetch",
        "ListMcpResources",
        "ReadMcpResource",
        "Task",
    }
    read_only_bash_keywords = {
        "ls", "find", "grep", "cat", "head", "tail",
        "wc", "sort", "uniq", "cut", "awk", "sed",
        "tr", "diff", "file", "stat", "echo", "printf",
        "which", "whereis", "type", "pwd", "env", "printenv",
        "date", "whoami", "id", "uname", "hostname",
        "git log", "git show", "git diff", "git status",
        "git branch", "git tag", "git blame",
        "npm list", "pip list", "pip show",
        "tree", "du", "df",
    }

    class _Allow:
        behavior = "allow"

    class _Deny:
        behavior = "deny"
        message = "Tool not permitted for memory extraction"

    async def can_use(
        tool: Any,
        tool_input: dict[str, Any],
        context: Any = None,
        assistant_msg: Any = None,
        tool_use_id: str = "",
        force: Any = None,
    ) -> Any:
        tool_name = getattr(tool, "name", "")

        # Read-only safe tools: always allow
        if tool_name in safe_tools:
            return _Allow()

        # BashTool: only read-only commands
        if tool_name == "Bash":
            command = tool_input.get("command", "")
            if isinstance(command, str):
                cmd_stripped = command.strip()
                for kw in read_only_bash_keywords:
                    if cmd_stripped.startswith(kw):
                        return _Allow()
            return _Deny()

        # Edit/Write: only within memory directory
        if tool_name in ("Write", "Edit"):
            path = tool_input.get("file_path", tool_input.get("path", ""))
            if isinstance(path, str) and memory_dir:
                try:
                    from hare.memdir.paths import is_auto_mem_path
                    if is_auto_mem_path(path):
                        return _Allow()
                except ImportError:
                    pass
            return _Deny()

        # REPL tool: allow through — inner primitives re-check this callback
        if tool_name == "REPL":
            return _Allow()

        # All other tools: deny
        return _Deny()

    return can_use


# ---------------------------------------------------------------------------
# Initialization — closure-scoped state (TS initExtractMemories L296-587)
# ---------------------------------------------------------------------------


def init_extract_memories() -> None:
    """Initialize the memory extraction system with closure-scoped state.

    TS initExtractMemories(): creates a fresh closure capturing all mutable
    state (cursor position, overlap guard, pending context).  Call once at
    startup alongside other init_* functions, or per-test in beforeEach.

    Sets the module-level _extractor and _drainer variables that are
    consumed by execute_extract_memories() and drain_pending_extraction().
    """
    global _extractor, _drainer, _in_flight_extractions

    # --- Closure-scoped mutable state (matching TS L297-325) ---

    # Every promise handed out by the extractor that hasn't settled yet.
    _in_flight_extractions = set()

    # UUID of the last message processed — cursor so each run only considers
    # messages added since the previous extraction.
    last_memory_message_uuid: str | None = None

    # One-shot flag: once we log that the gate is disabled, don't repeat.
    has_logged_gate_failure = False

    # True while runExtraction is executing — prevents overlapping runs.
    in_progress = False

    # Counts eligible turns since the last extraction run.
    turns_since_last_extraction = 0

    # When a call arrives during an in-progress run, stash context for a
    # trailing extraction after the current one finishes.
    pending_context: dict[str, Any] | None = None

    # --- Inner extraction logic (TS runExtraction L329-522) ---

    async def _run_extraction(
        context: Any,
        append_system_message: Callable[..., Any] | None = None,
        is_trailing_run: bool = False,
    ) -> None:
        nonlocal last_memory_message_uuid, in_progress, turns_since_last_extraction
        nonlocal pending_context

        # Extract messages from context (dict / object dual pattern)
        messages: list[dict[str, Any]] = (
            list(context.get("messages") or [])
            if isinstance(context, dict)
            else list(getattr(context, "messages", []))
        )
        if not messages:
            return

        # Resolve memory directory
        try:
            from hare.memdir.paths import get_auto_mem_path
            memory_dir = get_auto_mem_path().rstrip(os.sep)
        except ImportError:
            return

        # Resolve team memory feature flag
        team_memory_enabled = False
        try:
            from hare.memdir.team_mem_paths import is_team_memory_enabled
            team_memory_enabled = is_team_memory_enabled()
        except ImportError:
            pass

        # Resolve skip_index flag (TS: tengu_moth_copse GrowthBook)
        skip_index = os.environ.get("HARE_MEMORY_SKIP_INDEX", "") == "1"

        new_message_count = count_model_visible_messages_since(
            messages, last_memory_message_uuid
        )

        # Mutual exclusion: when the main agent wrote memories, skip extraction
        # and advance the cursor past this range.
        if _has_memory_writes_since(messages, memory_dir, last_memory_message_uuid):
            try:
                log_for_debugging(
                    "[extractMemories] skipping — conversation already wrote to "
                    "memory files"
                )
            except NameError:
                pass
            last_msg = messages[-1] if messages else {}
            last_uuid = last_msg.get("uuid") if isinstance(last_msg, dict) else getattr(last_msg, "uuid", None)
            if last_uuid:
                last_memory_message_uuid = str(last_uuid)
            _log_event("tengu_extract_memories_skipped_direct_write", {
                "message_count": new_message_count,
            })
            return

        can_use_tool = _create_auto_mem_can_use_tool(memory_dir)

        # Build cache-safe params
        try:
            from hare.utils.forked_agent import create_cache_safe_params
            cache_safe_params = create_cache_safe_params(context)
        except ImportError:
            return

        # Throttle: only run extraction every N eligible turns.
        # Trailing runs skip this check — they process already-committed work.
        if not is_trailing_run:
            turns_since_last_extraction += 1
            threshold = _get_throttle_threshold()
            if turns_since_last_extraction < threshold:
                return

        turns_since_last_extraction = 0
        in_progress = True
        start_time = time.time() * 1000  # milliseconds

        try:
            log_for_debugging(
                f"[extractMemories] starting — {new_message_count} new messages, "
                f"memoryDir={memory_dir}"
            )

            # Pre-inject memory manifest so the agent doesn't waste a turn on ls.
            try:
                from hare.memdir.memory_scan import (
                    format_memory_manifest,
                    scan_memory_files,
                )
                existing = await scan_memory_files(memory_dir, None)
                existing_manifest = format_memory_manifest(existing)
            except Exception:
                existing_manifest = ""

            # Build the extraction prompt
            from hare.services.extract_memories.prompts import (
                build_extract_auto_only_prompt,
                build_extract_combined_prompt,
            )

            if team_memory_enabled:
                user_prompt = build_extract_combined_prompt(
                    new_message_count,
                    existing_manifest,
                    skip_index,
                )
            else:
                user_prompt = build_extract_auto_only_prompt(
                    new_message_count,
                    existing_manifest,
                    skip_index,
                )

            # Run forked agent
            from hare.utils.forked_agent import ForkedAgentParams, run_forked_agent

            result = await run_forked_agent(
                ForkedAgentParams(
                    prompt_messages=[{"type": "user", "message": {"content": user_prompt}}],
                    cache_safe_params=cache_safe_params,
                    can_use_tool=can_use_tool,
                    query_source="extract_memories",
                    fork_label="extract_memories",
                    max_turns=5,
                    skip_cache_write=True,
                    skip_transcript=True,
                )
            )

            # Advance cursor only after successful run
            last_msg = messages[-1] if messages else {}
            last_uuid = last_msg.get("uuid") if isinstance(last_msg, dict) else getattr(last_msg, "uuid", None)
            if last_uuid:
                last_memory_message_uuid = str(last_uuid)

            written_paths = extract_written_paths(result.messages)

            # Count assistant turns in agent output
            turn_count = sum(
                1 for m in result.messages
                if (
                    m.get("type") if isinstance(m, dict)
                    else getattr(m, "type", None)
                ) == "assistant"
            )

            # Compute cache performance
            total_usage = result.total_usage or {}
            total_input = (
                total_usage.get("input_tokens", 0)
                + total_usage.get("cache_creation_input_tokens", 0)
                + total_usage.get("cache_read_input_tokens", 0)
            )
            hit_pct = (
                f"{(total_usage.get('cache_read_input_tokens', 0) / total_input * 100):.1f}"
                if total_input > 0
                else "0.0"
            )
            log_for_debugging(
                f"[extractMemories] finished — {len(written_paths)} files written, "
                f"cache: read={total_usage.get('cache_read_input_tokens', 0)} "
                f"create={total_usage.get('cache_creation_input_tokens', 0)} "
                f"input={total_usage.get('input_tokens', 0)} ({hit_pct}% hit)"
            )

            if written_paths:
                log_for_debugging(
                    f"[extractMemories] memories saved: {', '.join(written_paths)}"
                )
            else:
                log_for_debugging("[extractMemories] no memories saved this run")

            # Filter out MEMORY.md entries — only count topic files
            memory_paths = [
                p for p in written_paths
                if os.path.basename(p) != "MEMORY.md"
            ]

            team_count = 0
            if team_memory_enabled:
                try:
                    from hare.memdir.team_mem_paths import is_team_mem_path
                    team_count = sum(1 for p in memory_paths if is_team_mem_path(p))
                except ImportError:
                    pass

            # Log extraction event (TS L472-485)
            duration_ms = int(time.time() * 1000 - start_time)
            _log_event("tengu_extract_memories_extraction", {
                "input_tokens": total_usage.get("input_tokens", 0),
                "output_tokens": total_usage.get("output_tokens", 0),
                "cache_read_input_tokens": total_usage.get("cache_read_input_tokens", 0),
                "cache_creation_input_tokens": total_usage.get("cache_creation_input_tokens", 0),
                "message_count": new_message_count,
                "turn_count": turn_count,
                "files_written": len(written_paths),
                "memories_saved": len(memory_paths),
                "team_memories_saved": team_count,
                "duration_ms": duration_ms,
            })

            log_for_debugging(
                f"[extractMemories] writtenPaths={len(written_paths)} "
                f"memoryPaths={len(memory_paths)} "
                f"appendSystemMessage defined={append_system_message is not None}"
            )

            # Notify the main agent about saved memories
            if memory_paths and append_system_message is not None:
                msg = _create_memory_saved_message(memory_paths, team_count)
                try:
                    append_system_message(msg)
                except Exception:
                    pass

        except Exception as error:
            # Extraction is best-effort — log but don't notify on error
            log_for_debugging(f"[extractMemories] error: {error}")
            duration_ms = int(time.time() * 1000 - start_time)
            _log_event("tengu_extract_memories_error", {
                "duration_ms": duration_ms,
            })

        finally:
            in_progress = False

            # Trailing extraction: if a call arrived while we were running,
            # process it with the stashed context.
            trailing = pending_context
            pending_context = None
            if trailing is not None:
                log_for_debugging(
                    "[extractMemories] running trailing extraction for stashed context"
                )
                await _run_extraction(
                    context=trailing["context"],
                    append_system_message=trailing.get("append_system_message"),
                    is_trailing_run=True,
                )

    # --- Public entry point (TS executeExtractMemoriesImpl L527-567) ---

    async def _execute_extract_memories_impl(
        context: Any,
        append_system_message: Callable[..., Any] | None = None,
    ) -> None:
        nonlocal has_logged_gate_failure, in_progress, pending_context

        if context is None:
            return

        # Guard: sub-agent only (TS: context.toolUseContext.agentId check)
        agent_id = (
            context.get("tool_use_context", {}).get("agentId")
            if isinstance(context, dict)
            else getattr(
                getattr(context, "tool_use_context", None), "agentId", None
            )
        )
        if agent_id:
            return  # don't extract from sub-agents

        # Feature gate check (TS: tengu_passport_quail)
        if os.environ.get("HARE_DISABLE_EXTRACT_MEMORIES") == "1":
            if os.environ.get("HARE_USER_TYPE") == "ant" and not has_logged_gate_failure:
                has_logged_gate_failure = True
                _log_event("tengu_extract_memories_gate_disabled", {})
            return

        # Check auto-memory is enabled
        try:
            from hare.memdir.paths import is_auto_memory_enabled
            if not is_auto_memory_enabled():
                return
        except ImportError:
            return

        # Skip in remote mode
        try:
            from hare.bootstrap.state import get_is_remote_mode
            if get_is_remote_mode():
                return
        except ImportError:
            pass

        # Concurrent guard: stash for trailing extraction
        if in_progress:
            log_for_debugging(
                "[extractMemories] extraction in progress — stashing for trailing run"
            )
            _log_event("tengu_extract_memories_coalesced", {})
            pending_context = {
                "context": context,
                "append_system_message": append_system_message,
            }
            return

        await _run_extraction(
            context=context,
            append_system_message=append_system_message,
            is_trailing_run=False,
        )

    # --- Set extractor (TS L569-577) ---

    async def _extractor_wrapper(
        context: Any,
        append_system_message: Callable[..., Any] | None = None,
    ) -> None:
        task = asyncio.ensure_future(
            _execute_extract_memories_impl(context, append_system_message)
        )
        _in_flight_extractions.add(task)
        try:
            await task
        finally:
            _in_flight_extractions.discard(task)

    _extractor = _extractor_wrapper

    # --- Set drainer (TS L579-586) ---

    async def _drainer_impl(timeout_ms: int = 60_000) -> None:
        if not _in_flight_extractions:
            return
        try:
            await asyncio.wait_for(
                asyncio.gather(*_in_flight_extractions, return_exceptions=True),
                timeout=timeout_ms / 1000.0,
            )
        except asyncio.TimeoutError:
            pass  # soft timeout — best-effort drain
        except Exception:
            pass  # drainer must never throw

    _drainer = _drainer_impl


# ---------------------------------------------------------------------------
# Main extraction function (keyword fallback — standalone usage)
# ---------------------------------------------------------------------------


async def extract_memories(
    messages: list[dict[str, Any]],
    *,
    model: str = "",
    memory_path: str = "",
) -> list[str]:
    """Extract memories from conversation messages (standalone / keyword fallback).

    This is the simple path used when the full initExtractMemories / forked-agent
    infrastructure is not available (e.g., testing, offline mode, or direct API
    calls).  It scans for explicit memory cues in assistant text blocks.

    For production use, prefer `execute_extract_memories()` which is wired
    through the initExtractMemories closure and uses the full forked-agent
    pipeline with prompt cache sharing.
    """
    memories: list[str] = []

    # Memory cue patterns that suggest the model is intentionally recording info
    memory_cues = [
        "remember", "note:", "important:", "key point:",
        "memory:", "save:", "to remember", "keep in mind",
        "reminder:", "notable:", "highlight:",
    ]

    for msg in messages:
        if msg.get("type") != "assistant":
            continue
        content = msg.get("message", {}).get("content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "text":
                continue
            text = block.get("text", "")
            text_lower = text.lower()
            if any(cue in text_lower for cue in memory_cues):
                # Extract the paragraph containing the memory cue
                paragraphs = text.split("\n\n")
                for para in paragraphs:
                    para_lower = para.lower()
                    if any(cue in para_lower for cue in memory_cues):
                        stripped = para.strip()
                        if len(stripped) > 500:
                            stripped = stripped[:497] + "..."
                        if stripped and stripped not in memories:
                            memories.append(stripped)

    return memories


# ---------------------------------------------------------------------------
# Public API: execute extraction (TS executeExtractMemories L598-603)
# ---------------------------------------------------------------------------


async def execute_extract_memories(
    context: Any = None,
    append_system_message: Any = None,
) -> None:
    """Run memory extraction at the end of a query loop.

    TS executeExtractMemories (L598-603): fire-and-forget entry point called
    from handleStopHooks.  No-ops until init_extract_memories() has been called.

    The extractor closure handles all gates internally:
    1. Sub-agent guard (skip if agentId is set)
    2. Feature flag check (tengu_passport_quail)
    3. Auto-memory enabled check
    4. Remote mode check
    5. Concurrent guard (stash for trailing run if in-progress)
    6. Mutex (skip if main agent wrote memories)
    7. Throttle (turnsSinceLastExtraction)
    8. Forked agent execution with cache-sharing
    """
    global _extractor
    if _extractor is None:
        # Extraction system not yet initialized — silent no-op
        return

    await _extractor(context, append_system_message)


# ---------------------------------------------------------------------------
# Public API: drain pending extractions (TS drainPendingExtraction L611-615)
# ---------------------------------------------------------------------------


async def drain_pending_extraction(timeout_ms: int | None = None) -> None:
    """Await all in-flight extractions (including trailing runs) with a soft timeout.

    TS drainPendingExtraction (L611-615): called by print.ts after the response
    is flushed but before the 5s graceful shutdown failsafe kicks in.  Allows
    forked agents to complete gracefully without blocking exit indefinitely.

    No-ops until init_extract_memories() has been called.

    Parameters
    ----------
    timeout_ms:
        Soft timeout in milliseconds (default 60_000 = 60s).  When expired,
        the drainer returns without waiting for remaining extractions.
    """
    global _drainer
    if _drainer is None:
        return
    await _drainer(timeout_ms if timeout_ms is not None else 60_000)


# ---------------------------------------------------------------------------
# Internal helpers: logging and events
# ---------------------------------------------------------------------------


def log_for_debugging(message: str, level: str = "info") -> None:
    """Log a debug message when CLAUDE_CODE_DEBUG is enabled.

    Mirrors TS logForDebugging from src/utils/debug.ts.
    """
    if os.environ.get("CLAUDE_CODE_DEBUG") == "1":
        import sys
        prefix = f"[{level.upper()}]" if level else "[DEBUG]"
        print(f"{prefix} {message}", file=sys.stderr)


def _log_event(event_name: str, data: dict[str, Any]) -> None:
    """Log an analytics event (mirrors TS logEvent).

    In production this would go to an analytics backend; in the Python port
    it logs to debug output when CLAUDE_CODE_DEBUG is set.
    """
    if not os.environ.get("CLAUDE_CODE_DEBUG"):
        return
    import json
    try:
        payload = json.dumps({"event": event_name, **data})
    except (TypeError, ValueError):
        payload = json.dumps({"event": event_name, "data": str(data)})
    log_for_debugging(f"[analytics] {payload}", level="telemetry")
