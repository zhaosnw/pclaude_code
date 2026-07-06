"""Port of: src/utils/bash/specs/srun.ts"""

from __future__ import annotations

from typing import Any

SRUN_SPEC: dict[str, Any] = {
    "name": "srun",
    "description": "Run a command on SLURM cluster nodes",
    "options": [
        {
            "name": ["-n", "--ntasks"],
            "description": "Number of tasks",
            "args": {
                "name": "count",
                "description": "Number of tasks to run",
            },
        },
        {
            "name": ["-N", "--nodes"],
            "description": "Number of nodes",
            "args": {
                "name": "count",
                "description": "Number of nodes to allocate",
            },
        },
    ],
    "args": {
        "name": "command",
        "description": "Command to run on the cluster",
        "isCommand": True,
    },
}
