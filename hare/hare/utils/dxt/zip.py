"""Zip helpers for DXT bundles.

Port of: src/utils/dxt/zip.ts
"""

from __future__ import annotations

import zipfile
from pathlib import Path


def extract_zip_archive(archive: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, "r") as zf:
        zf.extractall(dest)


def create_zip_archive(source_dir: Path, dest_zip: Path) -> None:
    with zipfile.ZipFile(dest_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in source_dir.rglob("*"):
            zf.write(path, path.relative_to(source_dir))
