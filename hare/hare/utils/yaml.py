"""YAML parsing wrapper (port of yaml.ts)."""

from __future__ import annotations

from typing import Any


def parse_yaml(input_str: str) -> Any:
    try:
        import yaml as pyyaml  # type: ignore[import-untyped]

        return pyyaml.safe_load(input_str)
    except ImportError as e:
        raise RuntimeError("YAML parsing requires PyYAML: pip install pyyaml") from e
