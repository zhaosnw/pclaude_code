"""
Tests for memdir/memdir.py — MemDir (memory-backed directory).
"""

from __future__ import annotations

import os
import tempfile

from hare.memdir.memdir import MemDir


class TestMemDir:
    def setup_method(self) -> None:
        self._tmp = tempfile.mkdtemp()
        self._memdir = MemDir(base_path=self._tmp)

    def teardown_method(self) -> None:
        import shutil

        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_write_and_read(self) -> None:
        self._memdir.write("test.txt", "hello world")
        content = self._memdir.read("test.txt")
        assert content == "hello world"

    def test_write_creates_parent_dirs(self) -> None:
        self._memdir.write("deep/nested/file.txt", "data")
        assert os.path.isfile(os.path.join(self._tmp, "deep", "nested", "file.txt"))

    def test_read_nonexistent_returns_none(self) -> None:
        assert self._memdir.read("nonexistent.txt") is None

    def test_exists_for_written_file(self) -> None:
        self._memdir.write("exists.txt", "content")
        assert self._memdir.exists("exists.txt") is True

    def test_exists_for_nonexistent(self) -> None:
        assert self._memdir.exists("nope.txt") is False

    def test_exists_for_external_file(self) -> None:
        path = os.path.join(self._tmp, "external.txt")
        with open(path, "w") as f:
            f.write("external")
        assert self._memdir.exists("external.txt") is True

    def test_read_falls_back_to_filesystem(self) -> None:
        path = os.path.join(self._tmp, "fs_file.txt")
        with open(path, "w") as f:
            f.write("filesystem content")
        content = self._memdir.read("fs_file.txt")
        assert content == "filesystem content"

    def test_list_files(self) -> None:
        self._memdir.write("a.txt", "a")
        self._memdir.write("sub/b.txt", "b")
        files = self._memdir.list_files()
        assert "a.txt" in files
        assert "sub/b.txt" in files

    def test_list_files_empty(self) -> None:
        files = self._memdir.list_files()
        assert files == []

    def test_delete_removes_file(self) -> None:
        self._memdir.write("delete_me.txt", "bye")
        assert self._memdir.exists("delete_me.txt")
        result = self._memdir.delete("delete_me.txt")
        assert result is True
        assert not self._memdir.exists("delete_me.txt")

    def test_delete_nonexistent_returns_false(self) -> None:
        result = self._memdir.delete("no_file.txt")
        assert result is False

    def test_delete_removes_from_cache(self) -> None:
        self._memdir.write("cached.txt", "data")
        assert self._memdir.read("cached.txt") == "data"  # fills cache
        self._memdir.delete("cached.txt")
        assert self._memdir.read("cached.txt") is None

    def test_write_overwrites_previous_content(self) -> None:
        self._memdir.write("overwrite.txt", "first")
        self._memdir.write("overwrite.txt", "second")
        assert self._memdir.read("overwrite.txt") == "second"

    def test_path_with_parent_reference(self) -> None:
        # write with "../" creates file at the normalized location
        self._memdir.write("some/path/../file.txt", "data")
        # "some/path/../file.txt" resolves to "some/file.txt"
        assert os.path.isfile(os.path.join(self._tmp, "some", "file.txt"))
