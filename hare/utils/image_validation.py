"""API boundary image size validation — port of `imageValidation.ts`."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

API_IMAGE_MAX_BASE64_SIZE = 5 * 1024 * 1024  # 5 MiB string length safety cap


@dataclass
class OversizedImage:
    index: int
    size: int


class ImageSizeError(Exception):
    def __init__(self, oversized: list[OversizedImage], max_size: int) -> None:
        self.oversized = oversized
        self.max_size = max_size
        if len(oversized) == 1:
            o = oversized[0]
            msg = (
                f"Image base64 size ({o.size} bytes) exceeds API limit ({max_size}). "
                "Please resize the image before sending."
            )
        else:
            parts = ", ".join(f"Image {o.index}: {o.size} bytes" for o in oversized)
            msg = f"{len(oversized)} images exceed the API limit ({max_size}): {parts}. Please resize these images before sending."
        super().__init__(msg)


def _is_base64_image_block(block: Any) -> bool:
    if not isinstance(block, dict):
        return False
    if block.get("type") != "image":
        return False
    src = block.get("source")
    if not isinstance(src, dict):
        return False
    return src.get("type") == "base64" and isinstance(src.get("data"), str)


def validate_images_for_api(messages: list[Any]) -> None:
    oversized: list[OversizedImage] = []
    image_index = 0
    for msg in messages:
        if not isinstance(msg, dict) or msg.get("type") != "user":
            continue
        inner = msg.get("message")
        if not isinstance(inner, dict):
            continue
        content = inner.get("content")
        if isinstance(content, str) or not isinstance(content, list):
            continue
        for block in content:
            if _is_base64_image_block(block):
                image_index += 1
                data = block["source"]["data"]
                assert isinstance(data, str)
                if len(data) > API_IMAGE_MAX_BASE64_SIZE:
                    oversized.append(OversizedImage(index=image_index, size=len(data)))
    if oversized:
        raise ImageSizeError(oversized, API_IMAGE_MAX_BASE64_SIZE)
