"""
Parsed command facade (tree-sitter primary, regex fallback).

Port of: src/utils/bash/ParsedCommand.ts

The lightweight struct :class:`hare.utils.bash.parser.ParsedCommand` in
``parser.py`` is unrelated; this module exposes async parsing with
:class:`IParsedCommand` implementations.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from hare.utils.bash.bash_parser import TsNode
from hare.utils.bash.parser import _split_on_pipe
from hare.utils.bash.tree_sitter_analysis import TreeSitterAnalysis, analyze_command


@dataclass
class OutputRedirection:
    target: str
    operator: str  # '>' | '>>'


def _extract_output_redirections(cmd: str) -> tuple[str, list[OutputRedirection], bool]:
    """Best-effort ``>`` / ``>>`` extraction (TS parity is shell-quote based)."""
    redirections: list[OutputRedirection] = []
    if ">" not in cmd:
        return cmd, redirections, False
    stripped = re.sub(r"[ \t]*>[>]?[ \t]*(\S+)", lambda m: "", cmd)
    for m in re.finditer(r"(\S*?)(>>?)(\s*)(\S+)", cmd):
        op = m.group(2)
        target = m.group(4)
        if op in (">", ">>"):
            redirections.append(OutputRedirection(target=target, operator=op))
    dangerous = not redirections and ">" in cmd
    return stripped.strip() or cmd, redirections, dangerous


@runtime_checkable
class IParsedCommand(Protocol):
    @property
    def original_command(self) -> str: ...

    def __str__(self) -> str: ...

    def get_pipe_segments(self) -> list[str]: ...

    def without_output_redirections(self) -> str: ...

    def get_output_redirections(self) -> list[OutputRedirection]: ...

    def get_tree_sitter_analysis(self) -> TreeSitterAnalysis | None: ...


class RegexParsedCommandDeprecated:
    """Regex / shlex fallback when tree-sitter is unavailable."""

    __slots__ = ("_original_command",)

    def __init__(self, command: str) -> None:
        self._original_command = command

    @property
    def original_command(self) -> str:
        return self._original_command

    def __str__(self) -> str:
        return self._original_command

    def get_pipe_segments(self) -> list[str]:
        parts = _split_on_pipe(self._original_command)
        return parts if parts else [self._original_command]

    def without_output_redirections(self) -> str:
        if ">" not in self._original_command:
            return self._original_command
        cmd_wo, redirs, _ = _extract_output_redirections(self._original_command)
        return cmd_wo if redirs else self._original_command

    def get_output_redirections(self) -> list[OutputRedirection]:
        _, redirs, _ = _extract_output_redirections(self._original_command)
        return redirs

    def get_tree_sitter_analysis(self) -> TreeSitterAnalysis | None:
        return None


def _visit_nodes(node: TsNode, visitor: Any) -> None:
    visitor(node)
    for child in node.children:
        _visit_nodes(child, visitor)


def _extract_pipe_positions(root: TsNode) -> list[int]:
    positions: list[int] = []

    def visit(node: TsNode) -> None:
        if node.type == "pipeline":
            for child in node.children:
                if getattr(child, "type", None) == "|":
                    positions.append(child.start_index)

    _visit_nodes(root, visit)
    return sorted(positions)


@dataclass
class _RedirectionNode(OutputRedirection):
    start_index: int = 0
    end_index: int = 0


def _extract_redirection_nodes(root: TsNode) -> list[_RedirectionNode]:
    out: list[_RedirectionNode] = []

    def visit(node: TsNode) -> None:
        if node.type != "file_redirect":
            return
        children = list(node.children)
        op_node = next((c for c in children if c.type in (">", ">>")), None)
        word = next((c for c in children if c.type == "word"), None)
        if op_node and word:
            out.append(
                _RedirectionNode(
                    target=word.text,
                    operator=op_node.type,
                    start_index=node.start_index,
                    end_index=node.end_index,
                )
            )

    _visit_nodes(root, visit)
    return out


class TreeSitterParsedCommand:
    __slots__ = (
        "_original_command",
        "_command_bytes",
        "_pipe_positions",
        "_redirection_nodes",
        "_tree_sitter_analysis",
    )

    def __init__(
        self,
        command: str,
        pipe_positions: list[int],
        redirection_nodes: list[_RedirectionNode],
        tree_sitter_analysis: TreeSitterAnalysis,
    ) -> None:
        self._original_command = command
        self._command_bytes = command.encode("utf-8")
        self._pipe_positions = pipe_positions
        self._redirection_nodes = redirection_nodes
        self._tree_sitter_analysis = tree_sitter_analysis

    @property
    def original_command(self) -> str:
        return self._original_command

    def __str__(self) -> str:
        return self._original_command

    def get_pipe_segments(self) -> list[str]:
        if not self._pipe_positions:
            return [self._original_command]
        b = self._command_bytes
        segments: list[str] = []
        start = 0
        for pos in self._pipe_positions:
            chunk = b[start:pos].decode("utf-8").strip()
            if chunk:
                segments.append(chunk)
            start = pos + 1
        last = b[start:].decode("utf-8").strip()
        if last:
            segments.append(last)
        return segments

    def without_output_redirections(self) -> str:
        if not self._redirection_nodes:
            return self._original_command
        b = bytearray(self._command_bytes)
        for redir in sorted(
            self._redirection_nodes, key=lambda r: r.start_index, reverse=True
        ):
            del b[redir.start_index : redir.end_index]
        return re.sub(r"\s+", " ", bytes(b).decode("utf-8").strip())

    def get_output_redirections(self) -> list[OutputRedirection]:
        return [
            OutputRedirection(target=r.target, operator=r.operator)
            for r in self._redirection_nodes
        ]

    def get_tree_sitter_analysis(self) -> TreeSitterAnalysis:
        return self._tree_sitter_analysis


def build_parsed_command_from_root(command: str, root: TsNode) -> IParsedCommand:
    pipes = _extract_pipe_positions(root)
    redirs = _extract_redirection_nodes(root)
    analysis = analyze_command(root, command)
    return TreeSitterParsedCommand(command, pipes, redirs, analysis)


_last_cmd: str | None = None
_last_result: IParsedCommand | None = None


async def _tree_sitter_available() -> bool:
    """Hook for native parser; disabled until wired."""
    return False


async def _do_parse(command: str) -> IParsedCommand | None:
    if not command:
        return None
    if await _tree_sitter_available():
        try:
            from hare.utils.bash.bash_parser import parse_source

            data = await parse_source(command)
            if data is not None:
                return build_parsed_command_from_root(command, data)
        except Exception:
            pass
    return RegexParsedCommandDeprecated(command)


class ParsedCommand:
    """Namespace matching the TypeScript ``ParsedCommand.parse`` API."""

    @staticmethod
    async def parse(command: str) -> IParsedCommand | None:
        global _last_cmd, _last_result
        if command == _last_cmd and _last_result is not None:
            return _last_result
        _last_cmd = command
        _last_result = await _do_parse(command)
        return _last_result
