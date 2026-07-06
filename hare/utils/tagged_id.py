"""Tagged ID encoding compatible with API tagged_id format (port of taggedId.ts)."""

from __future__ import annotations

BASE_58_CHARS = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
VERSION = "01"
_ENCODED_LENGTH = 22


def _base58_encode(n: int) -> str:
    base = len(BASE_58_CHARS)
    result = [BASE_58_CHARS[0]] * _ENCODED_LENGTH
    i = _ENCODED_LENGTH - 1
    value = n
    while value > 0:
        rem = value % base
        result[i] = BASE_58_CHARS[rem]
        value //= base
        i -= 1
    return "".join(result)


def _uuid_to_int(uuid_str: str) -> int:
    hex_str = uuid_str.replace("-", "")
    if len(hex_str) != 32:
        raise ValueError(f"Invalid UUID hex length: {len(hex_str)}")
    return int(hex_str, 16)


def to_tagged_id(tag: str, uuid_str: str) -> str:
    n = _uuid_to_int(uuid_str)
    return f"{tag}_{VERSION}{_base58_encode(n)}"
