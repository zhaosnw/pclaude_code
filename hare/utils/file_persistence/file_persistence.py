"""
File persistence — manage temporary files and upload to remote storage.

Port of: src/utils/filePersistence/filePersistence.ts

Handles lifecycle of generated files (images, PDFs, tool results),
including temp file creation, size limits, cleanup, and upload.
"""

from __future__ import annotations

import asyncio
import atexit
import hashlib
import logging
import os
import secrets
import shutil
import tempfile
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Optional

logger = logging.getLogger(__name__)

# Default limits
MAX_TEMP_FILE_SIZE = 100 * 1024 * 1024  # 100 MB
MAX_TEMP_FILES = 100
TEMP_FILE_TTL_SECONDS = 3600  # 1 hour
MIN_DISK_FREE_BYTES = 10 * 1024 * 1024  # 10 MB safety margin

# Type for upload callbacks: (file_id, file_path, content_type, original_name) -> remote_url
UploadFn = Callable[[str, str, str, str], str]


@dataclass
class PersistedFile:
    path: str
    original_name: str
    content_type: str
    size_bytes: int
    created_at: float = field(default_factory=time.time)
    uploaded: bool = False
    remote_url: str = ""
    content_hash: str = ""  # SHA-256 hex digest for dedup / integrity


@dataclass
class FilePersistenceManager:
    """Manages temporary file lifecycle and optional remote upload."""

    _files: dict[str, PersistedFile] = field(default_factory=dict)
    _temp_dir: str = field(default="")
    _total_bytes: int = 0
    _upload_fn: Optional[UploadFn] = None
    _exit_registered: bool = field(default=False, repr=False)

    def __post_init__(self) -> None:
        if not self._temp_dir:
            self._temp_dir = os.path.join(tempfile.gettempdir(), "hare-files")
        os.makedirs(self._temp_dir, exist_ok=True)
        self._register_atexit()

    # ------------------------------------------------------------------
    # Core save / get / remove
    # ------------------------------------------------------------------

    def _check_disk_space(self, needed_bytes: int) -> None:
        """Raise OSError if free disk space is insufficient."""
        usage = shutil.disk_usage(self._temp_dir)
        if usage.free < needed_bytes + MIN_DISK_FREE_BYTES:
            raise OSError(
                f"Insufficient disk space: need {needed_bytes} bytes, "
                f"only {usage.free} available on {self._temp_dir}"
            )

    def save_file(
        self,
        data: bytes,
        original_name: str,
        content_type: str = "application/octet-stream",
    ) -> PersistedFile:
        """Save file data to temp storage and return metadata."""
        self._check_disk_space(len(data))

        content_hash = _sha256(data)
        existing = self._find_by_hash(content_hash)
        if existing is not None:
            logger.debug("File already persisted (hash match): %s", existing.path)
            return existing

        if self._total_bytes + len(data) > MAX_TEMP_FILE_SIZE:
            self._cleanup_oldest()

        file_id = _make_file_id(original_name)
        file_path = os.path.join(self._temp_dir, file_id)

        with open(file_path, "wb") as f:
            f.write(data)

        pf = PersistedFile(
            path=file_path,
            original_name=original_name,
            content_type=content_type,
            size_bytes=len(data),
            content_hash=content_hash,
        )
        self._files[file_id] = pf
        self._total_bytes += len(data)
        logger.debug("Saved file %s (%d bytes) -> %s", original_name, len(data), file_path)

        if len(self._files) > MAX_TEMP_FILES:
            self._cleanup_oldest()

        return pf

    async def save_file_async(
        self,
        data: bytes,
        original_name: str,
        content_type: str = "application/octet-stream",
    ) -> PersistedFile:
        """Async wrapper around save_file for non-blocking I/O on the write path."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, lambda: self.save_file(data, original_name, content_type)
        )

    def get_file(self, file_id: str) -> Optional[PersistedFile]:
        return self._files.get(file_id)

    def get_file_path(self, file_id: str) -> Optional[str]:
        pf = self._files.get(file_id)
        return pf.path if pf else None

    def read_file(self, file_id: str) -> Optional[bytes]:
        """Read file bytes back from disk."""
        pf = self._files.get(file_id)
        if pf is None or not os.path.isfile(pf.path):
            return None
        with open(pf.path, "rb") as f:
            return f.read()

    async def read_file_async(self, file_id: str) -> Optional[bytes]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: self.read_file(file_id))

    def remove_file(self, file_id: str) -> bool:
        """Remove a file from temp storage."""
        pf = self._files.pop(file_id, None)
        if pf is None:
            return False
        if os.path.isfile(pf.path):
            try:
                os.remove(pf.path)
            except OSError as exc:
                logger.warning("Failed to remove temp file %s: %s", pf.path, exc)
        self._total_bytes -= pf.size_bytes
        return True

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def _find_by_hash(self, content_hash: str) -> Optional[PersistedFile]:
        if not content_hash:
            return None
        for pf in self._files.values():
            if pf.content_hash == content_hash:
                return pf
        return None

    def _cleanup_oldest(self) -> None:
        """Remove oldest files until under limits."""
        sorted_files = sorted(self._files.items(), key=lambda x: x[1].created_at)
        while (self._total_bytes > MAX_TEMP_FILE_SIZE or len(self._files) > MAX_TEMP_FILES) and sorted_files:
            fid, _ = sorted_files[0]
            self.remove_file(fid)
            sorted_files = sorted_files[1:]

    def cleanup_expired(self, ttl_seconds: int = TEMP_FILE_TTL_SECONDS) -> int:
        """Remove files older than TTL. Returns count removed."""
        now = time.time()
        expired = [
            fid for fid, pf in self._files.items()
            if now - pf.created_at > ttl_seconds
        ]
        for fid in expired:
            self.remove_file(fid)
        if expired:
            logger.info("Cleaned up %d expired temp files", len(expired))
        return len(expired)

    def cleanup_all(self) -> int:
        """Remove all temp files. Returns count removed."""
        count = len(self._files)
        for fid in list(self._files.keys()):
            self.remove_file(fid)
        logger.info("Cleaned up all %d temp files", count)
        return count

    def cleanup_orphaned(self) -> int:
        """Remove any files on disk in _temp_dir that are not tracked in _files."""
        cleaned = 0
        try:
            for entry in os.scandir(self._temp_dir):
                if entry.is_file() and entry.name not in self._files:
                    try:
                        os.remove(entry.path)
                        cleaned += 1
                    except OSError:
                        pass
        except OSError:
            pass
        return cleaned

    # ------------------------------------------------------------------
    # Stats & listing
    # ------------------------------------------------------------------

    def get_stats(self) -> dict[str, Any]:
        return {
            "file_count": len(self._files),
            "total_bytes": self._total_bytes,
            "temp_dir": self._temp_dir,
            "uploaded_count": sum(1 for pf in self._files.values() if pf.uploaded),
        }

    def list_files(self) -> list[dict[str, Any]]:
        """Return lightweight metadata for all tracked files."""
        return [
            {
                "file_id": fid,
                "original_name": pf.original_name,
                "size_bytes": pf.size_bytes,
                "created_at": pf.created_at,
                "uploaded": pf.uploaded,
                "remote_url": pf.remote_url,
                "content_hash": pf.content_hash[:12] if pf.content_hash else "",
            }
            for fid, pf in self._files.items()
        ]

    # ------------------------------------------------------------------
    # Upload
    # ------------------------------------------------------------------

    def set_upload_fn(self, upload_fn: UploadFn) -> None:
        """Register a sync upload callback: (id, path, content_type, name) -> url."""
        self._upload_fn = upload_fn

    def upload_file(self, file_id: str) -> Optional[str]:
        """Upload a single file using the registered callback. Returns remote URL or None."""
        pf = self._files.get(file_id)
        if pf is None:
            return None
        if pf.uploaded and pf.remote_url:
            return pf.remote_url
        if self._upload_fn is None:
            logger.warning("No upload callback registered; skipping upload for %s", file_id)
            return None

        try:
            url = self._upload_fn(file_id, pf.path, pf.content_type, pf.original_name)
            if url:
                pf.remote_url = url
                pf.uploaded = True
                logger.info("Uploaded %s -> %s", pf.original_name, url)
            return url
        except Exception as exc:
            logger.error("Upload failed for %s: %s", pf.original_name, exc)
            return None

    def upload_all_unuploaded(self) -> int:
        """Upload all files not yet uploaded. Returns count uploaded."""
        count = 0
        for file_id, pf in list(self._files.items()):
            if not pf.uploaded:
                if self.upload_file(file_id) is not None:
                    count += 1
        return count

    # ------------------------------------------------------------------
    # Lifetime hooks
    # ------------------------------------------------------------------

    def _register_atexit(self) -> None:
        if not self._exit_registered:
            atexit.register(self._on_exit)
            self._exit_registered = True

    def _on_exit(self) -> None:
        """Clean up all temp files on process exit (best-effort)."""
        try:
            self.cleanup_all()
        except Exception:
            pass  # Never raise during interpreter shutdown

    # ------------------------------------------------------------------
    # Context manager for single temp files
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def temp_file(
        self,
        data: bytes,
        suffix: str = ".bin",
        prefix: str = "hare-",
    ) -> AsyncIterator[str]:
        """Async context manager: creates a temp file, yields its path, cleans up on exit.

        Usage:
            async with manager.temp_file(image_bytes, suffix=".png") as path:
                await some_api.upload(path)
        """
        file_id = _make_file_id(suffix.lstrip("."))
        file_path = os.path.join(self._temp_dir, file_id)
        try:
            with open(file_path, "wb") as f:
                f.write(data)
            yield file_path
        finally:
            if os.path.isfile(file_path):
                try:
                    os.remove(file_path)
                except OSError:
                    pass


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _make_file_id(original_name: str) -> str:
    """Create a unique file ID from original name and timestamp."""
    ext = os.path.splitext(original_name)[1] or ".bin"
    return f"{secrets.token_hex(8)}_{int(time.time())}{ext}"


# ------------------------------------------------------------------
# Global instance
# ------------------------------------------------------------------

_instance: Optional[FilePersistenceManager] = None


def get_file_persistence_manager() -> FilePersistenceManager:
    global _instance
    if _instance is None:
        _instance = FilePersistenceManager()
    return _instance


async def run_file_persistence(
    turn_start_time: Any, signal: Any | None = None
) -> Optional[dict[str, Any]]:
    """Run file persistence upload for a turn.

    In BYOC (Bring Your Own Cloud) mode, uploads temp files to remote storage.
    Otherwise, just cleans up expired temp files.
    """
    manager = get_file_persistence_manager()
    manager.cleanup_expired()
    manager.cleanup_orphaned()

    if os.environ.get("CLAUDE_CODE_ENVIRONMENT_KIND") != "byoc":
        return manager.get_stats()

    # BYOC mode: upload files to remote storage via the registered callback.
    if manager._upload_fn is None:
        logger.warning("BYOC mode active but no upload callback registered; skipping uploads")
        return manager.get_stats()

    uploaded = manager.upload_all_unuploaded()
    return {**manager.get_stats(), "uploaded": uploaded}
