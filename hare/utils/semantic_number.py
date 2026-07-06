"""
Numeric fields that accept decimal string literals. Port of src/utils/semanticNumber.ts.
"""

from __future__ import annotations

import re
from typing import Annotated, Any

from pydantic import BeforeValidator

_NUM = re.compile(r"^-?\d+(\.\d+)?$")


def _preprocess_number(v: Any) -> Any:
    if isinstance(v, str) and _NUM.fullmatch(v):
        n = float(v) if "." in v else int(v)
        if isinstance(n, float) and n.is_integer():
            return int(n)
        return n
    return v


def semantic_number(inner: type | None = None) -> Any:
    base: Any = float if inner is None else inner
    return Annotated[base, BeforeValidator(_preprocess_number)]
