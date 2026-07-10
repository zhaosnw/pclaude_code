"""
Main CLI application – sets up Commander, parses args, launches REPL or print mode.

Port of: src/main.tsx

The main() function in the TS source builds a Commander program with all
subcommands and options, then renders the REPL via React/Ink. In this
Python port, we use argparse and a simple REPL loop.
"""

from __future__ import annotations

import argparse
import os
import sys
import warnings
from typing import Any, Optional
from uuid import uuid4

# Suppress pydantic serializer warnings
warnings.filterwarnings("ignore", message="Pydantic serializer")
warnings.filterwarnings("ignore", category=UserWarning)

VERSION = "2.1.88"  # inline to avoid namespace-package import issue
from hare.bootstrap.state import (
    get_session_id,
    set_is_non_interactive_session,
    set_original_cwd,
    set_project_root,
)
from hare.commands import get_commands
from hare.session_setup import setup
from hare.tools import get_tools
from hare.tool import get_empty_tool_permission_context
from hare.utils.cwd import get_cwd, set_cwd


def _render_engine_event(
    msg: dict[str, Any], *, print_result_only: bool = False
) -> int:
    msg_type = msg.get("type", "")
    if msg_type == "assistant":
        if not print_result_only:
            message = msg.get("message")
            if message is not None:
                content = getattr(getattr(message, "message", None), "content", None)
                if isinstance(content, str):
                    text = content
                    if text:
                        print(text, end="", flush=True)
                elif isinstance(content, list):
                    for block in content:
                        block_type = (
                            block.get("type")
                            if isinstance(block, dict)
                            else getattr(block, "type", None)
                        )
                        if block_type == "text":
                            t = (
                                block.get("text", "")
                                if isinstance(block, dict)
                                else getattr(block, "text", "")
                            )
                            if t:
                                print(t, end="", flush=True)
                        elif block_type == "thinking":
                            t = (
                                block.get("thinking", "")
                                if isinstance(block, dict)
                                else getattr(block, "thinking", "")
                            )
                            if t:
                                print(f"\n🧠 [思考] {t}", end="", flush=True)
                        elif block_type == "redacted_thinking":
                            print(f"\n🧠 [思考已编辑]", end="", flush=True)
                        elif block_type == "tool_use":
                            name = (
                                block.get("name", "unknown")
                                if isinstance(block, dict)
                                else getattr(block, "name", "unknown")
                            )
                            print(f"\n🔧 [工具调用] {name}", end="", flush=True)
                        elif block_type == "tool_result":
                            print(f"\n📋 [工具结果]", end="", flush=True)
    elif msg_type == "stream_event":
        if not print_result_only:
            event_data = msg.get("event", msg.get("data", ""))
            if event_data:
                print(f"\n📡 [流事件]", end="", flush=True)
    elif msg_type == "progress":
        if not print_result_only:
            progress_data = msg.get("progress", {})
            if isinstance(progress_data, dict):
                ptype = progress_data.get("type", "")
                if ptype == "tool_use":
                    print(f"\n⏳ [执行中] {progress_data.get('tool_name', '')}", end="", flush=True)
    elif msg_type == "system":
        subtype = msg.get("subtype", "")
        if subtype == "init":
            # Don't print init messages
            pass
        elif subtype in ("compact_boundary", "api_error", "warning"):
            content = msg.get("content", "")
            if content:
                print(f"\n⚠️  [{subtype}] {content}", end="", flush=True)
    elif msg_type == "result":
        if not print_result_only:
            print()  # newline terminating the streamed assistant output
        else:
            # result-only mode streamed nothing; print(result_text) supplies the
            # single trailing newline. Claude Code prints result_text + '\n'
            # unconditionally — for an empty result that's just '\n', so do NOT
            # guard on truthiness (would diverge: hare '' vs reference '\n').
            print(msg.get("result", ""))
        if msg.get("is_error"):
            for err in msg.get("errors", []):
                print(f"Error: {err}")
            return 1
    return 0


async def cli_main(args: list[str] | None = None) -> None:
    """
    Main CLI entry point. Mirrors the main() export from src/main.tsx.

    In the TS source this:
    1. Builds a Commander program with all CLI options
    2. Parses arguments
    3. Calls setup()
    4. Either renders the REPL (interactive) or runs in print mode (non-interactive)
    """
    parser = argparse.ArgumentParser(
        prog="hare",
        description=f"Hare CLI v{VERSION} – Python port of Hare",
    )
    parser.add_argument(
        "--version", "-v", action="version", version=f"{VERSION} (Hare)"
    )
    parser.add_argument(
        "-p",
        "--print",
        dest="print_mode",
        metavar="PROMPT",
        help="Run in non-interactive (print) mode with the given prompt",
    )
    parser.add_argument(
        "-c",
        "--continue",
        dest="continue_session",
        action="store_true",
        help="Continue the most recent session",
    )
    parser.add_argument(
        "--resume",
        default=None,
        metavar="SESSION_ID",
        help="Resume a specific session by ID or JSONL file path",
    )
    parser.add_argument(
        "--fork-session",
        action="store_true",
        help="Fork the resumed session (create new session ID)",
    )
    parser.add_argument("--model", default=None, help="Model to use")
    parser.add_argument(
        "--max-turns", type=int, default=None, help="Max turns for the query loop"
    )
    parser.add_argument(
        "--permission-mode",
        default="default",
        choices=["default", "acceptEdits", "bypassPermissions", "plan"],
        help="Permission mode",
    )
    parser.add_argument("--verbose", action="store_true", help="Verbose output")
    parser.add_argument("--bare", action="store_true", help="Bare/simple mode")
    parser.add_argument("--cwd", default=None, help="Working directory")
    parser.add_argument("--system-prompt", default=None, help="Custom system prompt")
    parser.add_argument(
        "--append-system-prompt", default=None, help="Append to system prompt"
    )
    parser.add_argument(
        "--output-format",
        default=None,
        choices=["text", "json", "stream-json"],
        help="Output format for print mode",
    )
    parser.add_argument(
        "--allowed-tools", default=None, help="Comma-separated list of tools to allow"
    )
    parser.add_argument(
        "--disallowed-tools",
        default=None,
        help="Comma-separated list of tools to disallow",
    )
    parser.add_argument(
        "--mcp-config", default=None, nargs="*", help="MCP server configuration files"
    )
    parser.add_argument("prompt", nargs="*", help="Prompt text (non-interactive mode)")

    parsed = parser.parse_args(args)

    if parsed.mcp_config:
        from hare.services.mcp.config import validate_mcp_config_file

        for config_path in parsed.mcp_config:
            errors = validate_mcp_config_file(config_path)
            if errors:
                print("Error: Invalid MCP configuration:", file=sys.stderr)
                for error in errors:
                    print(error, file=sys.stderr)
                sys.exit(1)

    # Set working directory
    cwd = parsed.cwd or os.getcwd()
    set_cwd(cwd)
    set_original_cwd(cwd)
    set_project_root(cwd)

    # --- Config / env (port of ``entrypoints/init.ts`` + ``main.tsx`` print-path) ---
    # 1. ``enableConfigs()``  2. ``applySafeConfigEnvironmentVariables``  3. early CA bundle
    # 4. ``applyConfigEnvironmentVariables`` (implicit trust for this CLI; matches ``-p``)
    try:
        from hare.utils.config_full import enable_configs

        enable_configs()
    except ImportError:
        pass

    from hare.utils.ca_certs_config import apply_extra_ca_certs_from_config
    from hare.utils.managed_env import (
        apply_config_environment_variables,
        apply_safe_config_environment_variables,
    )

    apply_safe_config_environment_variables(project_dir=cwd)
    apply_extra_ca_certs_from_config()
    apply_config_environment_variables(project_dir=cwd)

    # Run setup
    await setup(
        cwd=cwd,
        permission_mode=parsed.permission_mode,
    )

    # Resolve the prompt and mode. Sources, in order: -p/--print value, a
    # positional prompt, or — when stdin is piped (not a TTY) — stdin itself
    # (Claude Code reads stdin as the prompt for `echo "..." | claude`). An
    # explicit -p (even empty) and a non-TTY stdin both mean non-interactive.
    # Resolved BEFORE the resume branch so `-p "x" --resume/--continue` can run
    # headlessly against the restored conversation instead of dropping to a REPL.
    if parsed.print_mode is not None:
        prompt: Optional[str] = parsed.print_mode
    elif parsed.prompt:
        prompt = " ".join(parsed.prompt)
    else:
        prompt = None

    stdin_piped = not sys.stdin.isatty()
    if (prompt is None or not prompt.strip()) and stdin_piped:
        try:
            stdin_text = sys.stdin.read()
        except Exception:
            stdin_text = ""
        if stdin_text.strip():
            prompt = stdin_text

    non_interactive = (
        parsed.print_mode is not None or bool(parsed.prompt) or stdin_piped
    )
    output_format = parsed.output_format or "text"

    if non_interactive:
        set_is_non_interactive_session(True)

    # Reject an empty/whitespace prompt cleanly instead of sending it to the
    # model (Claude Code errors here; sending it caused wasted turns/timeouts).
    _empty_prompt_msg = (
        "Error: Input must be provided either through a prompt argument "
        "or stdin when using --print"
    )

    # --- Resume / continue path ---
    if parsed.continue_session or parsed.resume:
        resume_prompt: Optional[str] = None
        if non_interactive:
            if prompt is None or not prompt.strip():
                print(_empty_prompt_msg, file=sys.stderr)
                sys.exit(1)
            resume_prompt = prompt
        await _resume_existing_session(
            session_id=parsed.resume,
            use_continue=parsed.continue_session,
            fork_session=parsed.fork_session,
            model=parsed.model,
            verbose=parsed.verbose,
            system_prompt=parsed.system_prompt,
            append_system_prompt=parsed.append_system_prompt,
            non_interactive=non_interactive,
            print_prompt=resume_prompt,
            output_format=output_format,
        )
        return

    if non_interactive:
        # Non-interactive (print) mode — aligns with print path in src/main.tsx
        if prompt is None or not prompt.strip():
            print(_empty_prompt_msg, file=sys.stderr)
            sys.exit(1)
        await _run_print_mode(
            prompt=prompt,
            model=parsed.model,
            max_turns=parsed.max_turns,
            verbose=parsed.verbose,
            system_prompt=parsed.system_prompt,
            append_system_prompt=parsed.append_system_prompt,
            output_format=output_format,
        )
    else:
        # Interactive REPL mode — aligns with React/Ink REPL in src/main.tsx
        await _run_repl(
            model=parsed.model,
            verbose=parsed.verbose,
            system_prompt=parsed.system_prompt,
            append_system_prompt=parsed.append_system_prompt,
        )


async def _resume_existing_session(
    session_id: str | None = None,
    use_continue: bool = False,
    fork_session: bool = False,
    model: str | None = None,
    verbose: bool = False,
    system_prompt: str | None = None,
    append_system_prompt: str | None = None,
    non_interactive: bool = False,
    print_prompt: str | None = None,
    output_format: str = "text",
) -> None:
    """Resume an existing session.

    Port of the --continue/--resume path in main.tsx.
    """
    try:
        from hare.utils.conversation_recovery import load_conversation_for_resume

        # Determine source: --resume takes a session_id or .jsonl path
        source = None
        source_jsonl_file = None
        if session_id:
            if session_id.endswith(".jsonl"):
                source_jsonl_file = session_id
            else:
                source = session_id
        elif use_continue:
            # --continue: load most recent session
            source = None

        result = await load_conversation_for_resume(source, source_jsonl_file)
    except ImportError:
        print("Error: conversation recovery module not available.")
        return
    except Exception as e:
        print(f"Error loading session: {e}")
        return

    if result is None:
        print("No session found to resume.")
        return

    loaded_messages = result.get("messages", [])
    loaded_session_id = result.get("sessionId")
    turn_interruption = result.get("turnInterruptionState", {})

    if not loaded_session_id:
        print("Error: could not determine session ID from loaded data.")
        return

    # Switch session (or fork)
    from hare.bootstrap.state import get_session_id, set_session_id

    if fork_session:
        # Fork: keep the loaded messages but generate a new session ID
        new_sid = str(uuid4())
        set_session_id(new_sid)
        print(f"Forked session: {loaded_session_id} -> {new_sid}")
    else:
        set_session_id(loaded_session_id)

    # Adopt the resumed session file
    try:
        from hare.utils.session_storage import adopt_resumed_session_file

        adopt_resumed_session_file()
    except ImportError:
        pass

    # Restore state from metadata
    try:
        from hare.bootstrap.state import (
            set_agent_name,
            set_agent_color,
            set_agent_setting,
            set_custom_title,
            set_tag,
            set_mode,
            set_worktree_session,
        )

        if result.get("agentName"):
            set_agent_name(result["agentName"])
        if result.get("agentColor"):
            set_agent_color(result["agentColor"])
        if result.get("agentSetting"):
            set_agent_setting(result["agentSetting"])
        if result.get("customTitle"):
            set_custom_title(result["customTitle"])
        if result.get("tag"):
            set_tag(result["tag"])
        if result.get("mode"):
            set_mode(result["mode"])
        if result.get("worktreeSession"):
            set_worktree_session(result["worktreeSession"])
    except (ImportError, AttributeError):
        pass

    # Start REPL with loaded messages
    from hare.query_engine import QueryEngine, QueryEngineConfig
    from hare.tool import get_empty_tool_permission_context
    from hare.tools import get_tools
    from hare.commands import get_commands

    permission_context = get_empty_tool_permission_context()
    tools = get_tools(permission_context)
    commands = await get_commands(os.getcwd())

    engine = QueryEngine(
        QueryEngineConfig(
            cwd=os.getcwd(),
            tools=tools,
            commands=commands,
            can_use_tool=_default_can_use_tool,
            get_app_state=lambda: {},
            set_app_state=lambda f: None,
            user_specified_model=model,
            verbose=verbose,
            custom_system_prompt=system_prompt,
            append_system_prompt=append_system_prompt,
            # Seed the restored conversation so the next turn carries prior
            # context. Without this the loaded messages were counted/printed
            # but discarded, and resume started from an empty conversation.
            initial_messages=loaded_messages,
        )
    )

    # Headless resume (`-p "x" --resume/--continue`): run the prompt once against
    # the restored conversation and emit in the requested format, then exit — do
    # NOT print interactive banners (they would corrupt json/stream-json stdout)
    # or drop into the REPL.
    if non_interactive and print_prompt is not None:
        exit_code = await _emit_print_stream(
            engine.submit_message(print_prompt), output_format
        )
        if exit_code:
            sys.exit(exit_code)
        return

    print(f"\nHare v{VERSION} — Session resumed: {get_session_id()}")
    print(f"Loaded {len(loaded_messages)} messages.")
    if turn_interruption.get("kind") == "interrupted_prompt":
        print("(Turn was interrupted — model will continue)")

    print("Type /help for available commands, or enter a prompt.\n")

    while True:
        try:
            user_input = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        if not user_input:
            continue

        if user_input.lower() in ("/exit", "/quit"):
            print("Goodbye!")
            break

        if user_input.startswith("/"):
            cmd_name = user_input.split()[0][1:]
            handled = await _handle_builtin_command(
                cmd_name, user_input, commands, engine
            )
            if handled:
                continue
            from hare.commands import find_command

            cmd = find_command(cmd_name, commands)
            if cmd is None:
                print(f"Unknown command: /{cmd_name}")
                continue
            if cmd.type == "local":
                try:
                    result = await cmd.call(user_input, {})
                    text = result.get("text", "")
                    if text:
                        print(text)
                except Exception as e:
                    print(f"Error: {e}")
                continue

        async for msg in engine.submit_message(user_input):
            _render_engine_event(msg)


def _to_jsonable(obj: Any) -> Any:
    """Recursively convert engine events into JSON-serializable form.

    The result event carries dataclasses (e.g. usage NonNullableUsage); json.dumps
    with default=str would stringify them. Convert dataclasses/objects to dicts so
    --output-format json emits structured fields, not "NonNullableUsage(...)".
    """
    import dataclasses

    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return _to_jsonable(dataclasses.asdict(obj))
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(v) for v in obj]
    if hasattr(obj, "__dict__"):
        return {k: _to_jsonable(v) for k, v in vars(obj).items() if not k.startswith("_")}
    return str(obj)


def _align_result_schema(result: dict[str, Any]) -> dict[str, Any]:
    """Map hare's result event onto Claude Code's --output-format json contract
    so SDK consumers see the same top-level keys: rename model_usage->modelUsage
    and add the keys the reference always emits (telemetry/internal fields whose
    exact values are environment-specific, so we supply stable defaults)."""
    if not isinstance(result, dict) or result.get("type") != "result":
        return result
    r = dict(result)
    if "model_usage" in r and "modelUsage" not in r:
        r["modelUsage"] = r.pop("model_usage")
    r.setdefault("modelUsage", {})
    r.setdefault("api_error_status", None)
    r.setdefault("ttft_ms", 0)
    r.setdefault("time_to_request_ms", 0)
    r.setdefault("fast_mode_state", "off")
    r.setdefault(
        "terminal_reason", "completed" if not r.get("is_error") else r.get("subtype", "error")
    )
    return r


async def _emit_print_stream(stream: Any, output_format: str = "text") -> int:
    """Render a model-message stream (from QueryEngine.submit_message or the SDK
    HareClient.stream, which share the same event shape) to stdout in the given
    print output format. Returns the process exit code (1 on an error result).

    Shared by the normal print path and the headless --resume/--continue path so
    both honor text / json / stream-json identically.
    """
    if output_format in ("json", "json-compact"):
        from hare.cli.print_handler import print_result

        result_event = None
        async for msg in stream:
            if msg.get("type") == "result":
                result_event = msg
        if result_event is not None:
            print_result(_align_result_schema(_to_jsonable(result_event)), output_format)
            if result_event.get("is_error"):
                return 1
        return 0

    if output_format in ("stream-json", "ndjson"):
        from hare.cli.print_handler import print_ndjson

        exit_code = 0
        async for msg in stream:
            # Partial stream events are emitted only with --include-partial-messages
            # (Claude Code default suppresses them); emit message-level events only.
            if msg.get("type") == "stream_event":
                continue
            print_ndjson(_align_result_schema(_to_jsonable(msg)))
            if msg.get("type") == "result" and msg.get("is_error"):
                exit_code = 1
        return exit_code

    exit_code = 0
    async for msg in stream:
        exit_code = max(exit_code, _render_engine_event(msg, print_result_only=True))
    return exit_code


async def _run_print_mode(
    prompt: str,
    model: Optional[str] = None,
    max_turns: Optional[int] = None,
    verbose: bool = False,
    system_prompt: Optional[str] = None,
    append_system_prompt: Optional[str] = None,
    output_format: str = "text",
) -> None:
    """Run in non-interactive (print) mode. Mirrors the print path in main.tsx."""
    from hare.sdk import HareClient, HareClientOptions

    client = await HareClient.create(
        HareClientOptions(
            cwd=get_cwd(),
            model=model,
            max_turns=max_turns,
            verbose=verbose,
            system_prompt=system_prompt,
            append_system_prompt=append_system_prompt,
        )
    )

    exit_code = await _emit_print_stream(client.stream(prompt), output_format)
    if exit_code:
        sys.exit(exit_code)


async def _run_repl(
    model: Optional[str] = None,
    verbose: bool = False,
    system_prompt: Optional[str] = None,
    append_system_prompt: Optional[str] = None,
) -> None:
    """Run the interactive REPL. Simplified version of the React/Ink REPL."""
    from hare.query_engine import QueryEngine, QueryEngineConfig

    permission_context = get_empty_tool_permission_context()
    tools = get_tools(permission_context)
    commands = await get_commands(get_cwd())

    engine = QueryEngine(
        QueryEngineConfig(
            cwd=get_cwd(),
            tools=tools,
            commands=commands,
            can_use_tool=_default_can_use_tool,
            get_app_state=lambda: {},
            set_app_state=lambda f: None,
            user_specified_model=model,
            verbose=verbose,
            custom_system_prompt=system_prompt,
            append_system_prompt=append_system_prompt,
        )
    )

    print(f"\nHare v{VERSION} (Hare Python Port)")
    print(f"Session: {get_session_id()}")
    print(f"Working directory: {get_cwd()}")
    print("Type /help for available commands, or enter a prompt.\n")

    while True:
        try:
            user_input = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        if not user_input:
            continue

        if user_input.lower() in ("/exit", "/quit"):
            print("Goodbye!")
            break

        if user_input.startswith("/"):
            cmd_name = user_input.split()[0][1:]
            handled = await _handle_builtin_command(
                cmd_name, user_input, commands, engine
            )
            if handled:
                continue
            from hare.commands import find_command

            cmd = find_command(cmd_name, commands)
            if cmd is None:
                print(f"Unknown command: /{cmd_name}")
                print("Type /help for available commands.")
                continue
            if cmd.type == "local":
                try:
                    result = await cmd.call(user_input, {})
                    text = result.get("text", "")
                    if text:
                        print(text)
                except Exception as e:
                    print(f"Error: {e}")
                continue

        async for msg in engine.submit_message(user_input):
            _render_engine_event(msg)


async def _handle_builtin_command(
    name: str,
    raw_input: str,
    commands: list[Any],
    engine: Any,
) -> bool:
    """Handle built-in slash commands. Returns True if handled."""
    if name == "help":
        print("\nAvailable commands:\n")
        for cmd in commands:
            aliases = ""
            if cmd.aliases:
                aliases = f" (aliases: {', '.join('/' + a for a in cmd.aliases)})"
            print(f"  /{cmd.name:12s}  {cmd.description}{aliases}")
        print()
        return True

    if name == "exit" or name == "quit":
        print("Goodbye!")
        sys.exit(0)

    if name == "clear":
        os.system("cls" if os.name == "nt" else "clear")  # nosec B605
        return True

    if name == "cost":
        from hare.cost_tracker import get_total_cost, get_model_usage

        cost = get_total_cost()
        usage = get_model_usage()
        print(f"\nSession cost: ${cost:.4f}")
        print(f"  Input tokens:  {usage.get('input_tokens', 0):,}")
        print(f"  Output tokens: {usage.get('output_tokens', 0):,}")
        print()
        return True

    if name == "status":
        from hare.bootstrap.state import get_session_id
        from hare.utils.cwd import get_cwd

        print(f"\nSession:  {get_session_id()}")
        print(f"CWD:      {get_cwd()}")
        print(f"Version:  {VERSION}")
        print()
        return True

    if name == "model":
        parts = raw_input.split(maxsplit=1)
        if len(parts) > 1:
            new_model = parts[1].strip()
            engine._config.user_specified_model = new_model
            print(f"Model switched to: {new_model}")
        else:
            current = engine._config.user_specified_model or "(default)"
            print(f"Current model: {current}")
            print("Usage: /model <model-name>")
        return True

    if name == "compact":
        print("Compacting conversation... (stub — not yet implemented)")
        return True

    if name == "diff":
        import subprocess

        try:
            result = subprocess.run(
                ["git", "diff", "--stat"], capture_output=True, text=True, timeout=10
            )
            output = result.stdout.strip()
            print(f"\n{output if output else 'No changes.'}\n")
        except Exception as e:
            print(f"Error running git diff: {e}")
        return True

    return False


async def _default_can_use_tool(
    tool: Any,
    input: Any,
    context: Any,
    assistant_msg: Any,
    tool_use_id: str,
    force: Any,
) -> Any:
    """Default permission handler – allows all tools."""
    from hare.app_types.permissions import PermissionAllowDecision

    return PermissionAllowDecision(behavior="allow", updated_input=input)
