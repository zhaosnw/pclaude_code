#!/usr/bin/env python3
"""
Minimal Hare Python SDK one-shot (async ``HareClient``).

Typical layout: this repo directory contains ``pyproject.toml`` and the inner
Python package folder ``hare/``. Either install editable (recommended)::

  cd /path/to/this/repo
  pip install -e ".[anthropic]"
  python scripts/hare_sdk_ask.py "你是什么模型" --config scripts/deepseek_sdk_config.example.json

Or without install::

  cd /path/to/this/repo
  PYTHONPATH=. python scripts/hare_sdk_ask.py "Hello" --config /path/to/your.json

Copy ``scripts/deepseek_sdk_config.example.json`` to a path outside git, put
your real ``ANTHROPIC_AUTH_TOKEN``, and pass ``--config`` to that file.

Environment variables in ``env`` are applied before importing Hare (so the
Anthropic client and model resolution see them). Optional top-level
``effortLevel`` sets ``CLAUDE_CODE_EFFORT_LEVEL`` if not already in ``env``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _expand_user_path(p: Path) -> Path:
    """Expand ``~`` / env vars; fix fullwidth tilde ``～`` (U+FF5E) often pasted from IME."""
    s = os.fspath(p).strip()
    if s.startswith(
        "\uff5e"
    ):  # FULLWIDTH TILDE — not expanded by the shell or expanduser
        s = "~" + s[1:]
    return Path(os.path.expandvars(os.path.expanduser(s)))


def _ensure_package_on_path() -> None:
    root = str(_repo_root())
    if root not in sys.path:
        sys.path.insert(0, root)


def _extract_stream_text(ev: dict[str, object]) -> str:
    event = ev.get("event")
    if not isinstance(event, dict):
        data = ev.get("data")
        return str(data.get("text", "")) if isinstance(data, dict) else ""

    event_type = event.get("type")
    if event_type == "content_block_delta":
        delta = event.get("delta")
        if isinstance(delta, dict):
            return str(delta.get("text", "") or "")
    if event_type == "content_block_start":
        content_block = event.get("content_block")
        if isinstance(content_block, dict) and content_block.get("type") == "text":
            return str(content_block.get("text", "") or "")
    if event_type == "message_delta":
        delta = event.get("delta")
        if isinstance(delta, dict):
            return str(delta.get("text", "") or "")
    return ""


def apply_config_file(path: Path) -> None:
    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw)
    env = data.get("env")
    if isinstance(env, dict):
        for key, value in env.items():
            if not isinstance(key, str):
                continue
            s = "" if value is None else str(value).strip()
            if s:
                os.environ[key] = s
    effort = data.get("effortLevel")
    if isinstance(effort, str) and effort.strip():
        os.environ.setdefault("CLAUDE_CODE_EFFORT_LEVEL", effort.strip())


def _print_event(ev: dict[str, object]) -> None:
    t = ev.get("type", "?")
    if t == "assistant":
        msg = ev.get("message")
        text = ""
        if msg is not None and hasattr(msg, "message"):
            inner = getattr(msg, "message", None)
            content = getattr(inner, "content", None) if inner is not None else None
            if isinstance(content, list) and content:
                last = content[-1]
                if isinstance(last, dict) and last.get("type") == "text":
                    text = str(last.get("text", ""))
        print(text, end="", flush=True)
    elif t == "stream_event":
        text = _extract_stream_text(ev)
        if text:
            print(text, end="", flush=True)
    elif t == "result":
        print("\n--- result ---", file=sys.stderr)
        print(ev, file=sys.stderr)
    elif t == "error":
        print("\n--- error ---", file=sys.stderr)
        print(ev, file=sys.stderr)
    elif t == "system" and ev.get("subtype") == "init":
        print(f"[session model: {ev.get('model', '?')}]", file=sys.stderr)


async def amain() -> int:
    parser = argparse.ArgumentParser(description="Hare SDK one-shot ask/stream.")
    parser.add_argument(
        "prompt",
        nargs="?",
        default="Say hello in one short sentence.",
        help="User message (default: short hello).",
    )
    parser.add_argument(
        "--config",
        type=Path,
        metavar="PATH",
        help='JSON file with {"env": {...}, "effortLevel": optional}.',
    )
    parser.add_argument(
        "--cwd",
        type=Path,
        default=None,
        help="Project working directory (default: current directory).",
    )
    parser.add_argument(
        "--stream",
        action="store_true",
        help="Print assistant deltas to stdout; final result on stderr.",
    )
    parser.add_argument("--verbose", action="store_true", help="Log each event type.")
    args = parser.parse_args()

    if args.config is not None:
        config_path = _expand_user_path(args.config).resolve()
        if not config_path.is_file():
            print(f"Config not found: {args.config}", file=sys.stderr)
            if os.fspath(args.config).strip().startswith("\uff5e"):
                print(
                    "Hint: path starts with fullwidth tilde ～ (U+FF5E); use ASCII ~ for home.",
                    file=sys.stderr,
                )
            return 2
        apply_config_file(config_path)

    _ensure_package_on_path()

    try:
        from hare.utils.config_full import enable_configs

        enable_configs()
    except ImportError:
        pass

    from hare.sdk import HareClient, HareClientOptions
    from hare.session_setup import setup
    from hare.utils.cwd import set_cwd

    cwd = str(
        _expand_user_path(args.cwd).resolve() if args.cwd else Path.cwd().resolve()
    )
    set_cwd(cwd)
    await setup(cwd=cwd, permission_mode="default")

    model = os.environ.get("ANTHROPIC_MODEL") or None
    client = await HareClient.create(
        HareClientOptions(
            cwd=cwd,
            model=model,
            verbose=args.verbose,
        )
    )

    if args.stream:
        async for ev in client.stream(args.prompt):
            if args.verbose:
                print(ev.get("type"), file=sys.stderr)
            _print_event(ev)
        return 0

    final = await client.ask(args.prompt)
    print(json.dumps(final, default=str, ensure_ascii=False, indent=2))
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(amain()))


if __name__ == "__main__":
    main()
