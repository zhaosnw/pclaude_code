"""
Aggregated bash command specs (order matches TypeScript ``specs/index.ts``).

Port of: src/utils/bash/specs/index.ts
"""

from __future__ import annotations

from typing import Any

from hare.utils.bash.specs.alias import ALIAS_SPEC
from hare.utils.bash.specs.nohup import NOHUP_SPEC
from hare.utils.bash.specs.pyright import PYRIGHT_SPEC
from hare.utils.bash.specs.sleep import SLEEP_SPEC
from hare.utils.bash.specs.srun import SRUN_SPEC
from hare.utils.bash.specs.time import TIME_SPEC
from hare.utils.bash.specs.timeout import TIMEOUT_SPEC

BASH_SPEC_LIST: list[dict[str, Any]] = [
    PYRIGHT_SPEC,
    TIMEOUT_SPEC,
    SLEEP_SPEC,
    ALIAS_SPEC,
    NOHUP_SPEC,
    TIME_SPEC,
    SRUN_SPEC,
]
