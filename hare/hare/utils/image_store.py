"""On-disk image cache for pasted images — port of `imageStore.ts`."""

from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Any, Protocol

from hare.utils.debug import log_for_debugging
from hare.utils.env_utils import get_hare_config_home_dir

IMAGE_STORE_DIR = "image-cache"
MAX_STORED_IMAGE_PATHS = 200
_stored: dict[int, str] = {}


def _session_id() -> str:
    try:
        from hare.bootstrap.state import get_session_id  # type: ignore[import-not-found]

        return get_session_id()
    except ImportError:
        return "default"


def _store_dir() -> Path:
    return Path(get_hare_config_home_dir()) / IMAGE_STORE_DIR / _session_id()


def _image_path(image_id: int, media_type: str) -> str:
    ext = (media_type.split("/")[1] if "/" in media_type else "png") or "png"
    return str(_store_dir() / f"{image_id}.{ext}")


class PastedContent(Protocol):
    id: int
    type: str
    content: str
    media_type: str | None


def cache_image_path(content: Any) -> str | None:
    if getattr(content, "type", None) != "image":
        return None
    mt = getattr(content, "media_type", None) or "image/png"
    path = _image_path(int(content.id), str(mt))
    _evict()
    _stored[int(content.id)] = path
    return path


async def store_image(content: Any) -> str | None:
    if getattr(content, "type", None) != "image":
        return None
    try:
        _store_dir().mkdir(parents=True, exist_ok=True)
        mt = getattr(content, "media_type", None) or "image/png"
        path = _image_path(int(content.id), str(mt))
        raw = (
            base64.b64decode(content.content)
            if isinstance(content.content, str)
            else content.content
        )
        Path(path).write_bytes(raw)
        os.chmod(path, 0o600)
        _evict()
        _stored[int(content.id)] = path
        log_for_debugging(f"Stored image {content.id} to {path}")
        return path
    except Exception as e:
        log_for_debugging(f"Failed to store image: {e}")
        return None


async def store_images(pasted_contents: dict[int, Any]) -> dict[int, str]:
    out: dict[int, str] = {}
    for _k, content in pasted_contents.items():
        if getattr(content, "type", None) == "image":
            p = await store_image(content)
            if p:
                out[int(_k)] = p
    return out


def get_stored_image_path(image_id: int) -> str | None:
    return _stored.get(image_id)


def clear_stored_image_paths() -> None:
    _stored.clear()


def _evict() -> None:
    while len(_stored) >= MAX_STORED_IMAGE_PATHS:
        k = next(iter(_stored))
        del _stored[k]


async def cleanup_old_image_caches() -> None:
    base = Path(get_hare_config_home_dir()) / IMAGE_STORE_DIR
    cur = _session_id()
    try:
        for entry in base.iterdir():
            if entry.name == cur:
                continue
            try:
                import shutil

                shutil.rmtree(entry, ignore_errors=True)
                log_for_debugging(f"Cleaned up old image cache: {entry}")
            except OSError:
                pass
        if base.exists() and not any(base.iterdir()):
            base.rmdir()
    except OSError:
        pass
