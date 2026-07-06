"""
Linguist-style generated / vendored file detection.

Port of: src/utils/generatedFiles.ts
"""

from __future__ import annotations

import os
import re
from pathlib import PurePosixPath

EXCLUDED_FILENAMES = frozenset(
    x.lower()
    for x in (
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "bun.lockb",
        "bun.lock",
        "composer.lock",
        "gemfile.lock",
        "cargo.lock",
        "poetry.lock",
        "pipfile.lock",
        "shrinkwrap.json",
        "npm-shrinkwrap.json",
    )
)

EXCLUDED_EXTENSIONS = frozenset(
    x.lower()
    for x in (
        ".lock",
        ".min.js",
        ".min.css",
        ".min.html",
        ".bundle.js",
        ".bundle.css",
        ".generated.ts",
        ".generated.js",
        ".d.ts",
    )
)

EXCLUDED_DIRECTORIES = (
    "/dist/",
    "/build/",
    "/out/",
    "/output/",
    "/node_modules/",
    "/vendor/",
    "/vendored/",
    "/third_party/",
    "/third-party/",
    "/external/",
    "/.next/",
    "/.nuxt/",
    "/.svelte-kit/",
    "/coverage/",
    "/__pycache__/",
    "/.tox/",
    "/venv/",
    "/.venv/",
    "/target/release/",
    "/target/debug/",
)

EXCLUDED_FILENAME_PATTERNS = (
    re.compile(r"^.*\.min\.[a-z]+$", re.I),
    re.compile(r"^.*-min\.[a-z]+$", re.I),
    re.compile(r"^.*\.bundle\.[a-z]+$", re.I),
    re.compile(r"^.*\.generated\.[a-z]+$", re.I),
    re.compile(r"^.*\.gen\.[a-z]+$", re.I),
    re.compile(r"^.*\.auto\.[a-z]+$", re.I),
    re.compile(r"^.*_generated\.[a-z]+$", re.I),
    re.compile(r"^.*_gen\.[a-z]+$", re.I),
    re.compile(r"^.*\.pb\.(go|js|ts|py|rb)$", re.I),
    re.compile(r"^.*_pb2?\.py$", re.I),
    re.compile(r"^.*\.pb\.h$", re.I),
    re.compile(r"^.*\.grpc\.[a-z]+$", re.I),
    re.compile(r"^.*\.swagger\.[a-z]+$", re.I),
    re.compile(r"^.*\.openapi\.[a-z]+$", re.I),
)


def is_generated_file(file_path: str) -> bool:
    posix_path = file_path.replace(os.sep, "/")
    normalized_path = "/" + posix_path.lstrip("/")
    file_name = os.path.basename(file_path).lower()
    ext = PurePosixPath(file_path).suffix.lower()
    if file_name in EXCLUDED_FILENAMES:
        return True
    if ext in EXCLUDED_EXTENSIONS:
        return True
    parts = file_name.split(".")
    if len(parts) > 2:
        compound = "." + ".".join(parts[-2:])
        if compound in EXCLUDED_EXTENSIONS:
            return True
    for d in EXCLUDED_DIRECTORIES:
        if d in normalized_path:
            return True
    for pat in EXCLUDED_FILENAME_PATTERNS:
        if pat.match(file_name):
            return True
    return False


def filter_generated_files(files: list[str]) -> list[str]:
    return [f for f in files if not is_generated_file(f)]
