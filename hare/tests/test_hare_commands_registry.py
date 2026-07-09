from __future__ import annotations

import pytest

from hare.commands import get_commands


@pytest.mark.asyncio
async def test_get_commands_includes_impl_and_bundled_skill_commands() -> None:
    commands = await get_commands(".")
    names = {cmd.name for cmd in commands}
    assert "help" in names
    assert "compact" in names
    assert "verify" in names
