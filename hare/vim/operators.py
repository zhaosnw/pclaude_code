"""Vim operators (port of src/vim/operators.ts)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from hare.vim.cursor import Cursor
from hare.vim.motions import is_inclusive_motion, is_linewise_motion, resolve_motion
from hare.vim.text_objects import find_text_object
from hare.vim.vim_types import FindType, Operator


@dataclass
class OperatorContext:
    cursor: Cursor
    text: str
    set_text: Callable[[str], None]
    set_offset: Callable[[int], None]
    enter_insert: Callable[[int], None]
    get_register: Callable[[], str]
    set_register: Callable[[str, bool], None]
    get_last_find: Callable[[], tuple[FindType, str] | None]
    set_last_find: Callable[[FindType, str], None]
    record_change: Callable[[object], None]


def execute_operator_motion(
    op: Operator,
    motion: str,
    count: int,
    ctx: OperatorContext,
) -> None:
    target = resolve_motion(motion, ctx.cursor, count)
    if target.equals(ctx.cursor):
        return
    rng = _get_operator_range(ctx.cursor, target, motion, op)
    _apply_operator(op, rng[0], rng[1], ctx, rng[2])


def _get_operator_range(
    cursor: Cursor,
    target: Cursor,
    motion: str,
    op: Operator,
) -> tuple[int, int, bool]:
    from hare.vim.cursor import Cursor as C

    from_ = min(cursor.offset, target.offset)
    to = max(cursor.offset, target.offset)
    linewise = False
    if op == "change" and motion in ("w", "W"):
        return (from_, to + 1, False)
    if is_linewise_motion(motion):
        linewise = True
        text = cursor.text
        nn = text.find("\n", to)
        if nn == -1:
            to = len(text)
            if from_ > 0 and text[from_ - 1] == "\n":
                from_ -= 1
        else:
            to = nn + 1
    elif is_inclusive_motion(motion) and cursor.offset <= target.offset:
        c = C(cursor.text, to)
        to = c.next_offset(to)
    return (from_, to, linewise)


def _apply_operator(
    op: Operator,
    from_: int,
    to: int,
    ctx: OperatorContext,
    linewise: bool = False,
) -> None:
    content = ctx.text[from_:to]
    if linewise and not content.endswith("\n"):
        content += "\n"
    ctx.set_register(content, linewise)
    if op == "yank":
        ctx.set_offset(from_)
    elif op == "delete":
        new_text = ctx.text[:from_] + ctx.text[to:]
        ctx.set_text(new_text)
        ctx.set_offset(min(from_, max(0, len(new_text) - 1)))
    elif op == "change":
        new_text = ctx.text[:from_] + ctx.text[to:]
        ctx.set_text(new_text)
        ctx.enter_insert(from_)


def execute_operator_find(
    op: Operator,
    find_type: FindType,
    char: str,
    count: int,
    ctx: OperatorContext,
) -> None:
    off = ctx.cursor.find_character(char, find_type, count)
    if off is None:
        return
    target = Cursor(ctx.cursor.text, off)
    from_ = min(ctx.cursor.offset, target.offset)
    to = ctx.cursor.next_offset(max(ctx.cursor.offset, target.offset))
    _apply_operator(op, from_, to, ctx)


def execute_operator_text_obj(
    op: Operator,
    scope: str,
    obj_type: str,
    count: int,
    ctx: OperatorContext,
) -> None:
    _ = count
    r = find_text_object(ctx.text, ctx.cursor.offset, obj_type, scope == "inner")
    if not r:
        return
    _apply_operator(op, r[0], r[1], ctx)


def execute_line_op(op: Operator, count: int, ctx: OperatorContext) -> None:
    _ = (op, count, ctx)


def execute_x(count: int, ctx: OperatorContext) -> None:
    _ = (count, ctx)


def execute_replace(char: str, count: int, ctx: OperatorContext) -> None:
    _ = (char, count, ctx)


def execute_toggle_case(count: int, ctx: OperatorContext) -> None:
    _ = (count, ctx)


def execute_join(count: int, ctx: OperatorContext) -> None:
    _ = (count, ctx)


def execute_paste(after: bool, count: int, ctx: OperatorContext) -> None:
    _ = (after, count, ctx)


def execute_indent(direction: str, count: int, ctx: OperatorContext) -> None:
    _ = (direction, count, ctx)


def execute_open_line(direction: str, ctx: OperatorContext) -> None:
    _ = (direction, ctx)


def execute_operator_g(op: Operator, count: int, ctx: OperatorContext) -> None:
    _ = (op, count, ctx)


def execute_operator_gg(op: Operator, count: int, ctx: OperatorContext) -> None:
    _ = (op, count, ctx)
