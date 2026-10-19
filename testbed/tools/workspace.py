"""A trial's workspace folder. Names only, never paths (checks from v1/tools.py)."""

from __future__ import annotations

import re
from pathlib import Path

NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class WorkspaceError(Exception):
    pass


class Workspace:
    def __init__(self, root: Path, *, max_file_bytes: int, max_input_bytes: int):
        self.root = root.resolve()
        self.max_file_bytes = max_file_bytes
        self.max_input_bytes = max_input_bytes

    def _path(self, name: str, *, must_exist: bool) -> Path:
        if not isinstance(name, str) or not NAME.match(name) or ".." in name:
            raise WorkspaceError(f"Invalid file name {name!r}")
        path = self.root / name
        if path.is_symlink():
            raise WorkspaceError("Expected a regular file")
        if must_exist:
            if not path.is_file():
                raise WorkspaceError(f"No file named {name!r}")
            if path.stat().st_size > self.max_file_bytes:
                raise WorkspaceError("File size limit exceeded")
        elif path.exists() and not path.is_file():
            raise WorkspaceError("Expected a regular file")
        return path

    def _check_input(self, text: str) -> bytes:
        data = text.encode("utf-8")
        if len(data) > self.max_input_bytes:
            raise WorkspaceError("Input size limit exceeded")
        if len(data) > self.max_file_bytes:
            raise WorkspaceError("File size limit exceeded")
        return data

    def list(self) -> list[str]:
        return sorted(
            path.name for path in self.root.iterdir()
            if path.is_file() and not path.is_symlink()
        )

    def exists(self, name: str) -> bool:
        try:
            return self._path(name, must_exist=False).is_file()
        except WorkspaceError:
            return False

    def read(self, name: str) -> str:
        return self._path(name, must_exist=True).read_text(encoding="utf-8")

    def write(self, name: str, content: str) -> None:
        data = self._check_input(content)
        self._path(name, must_exist=False).write_bytes(data)

    def append(self, name: str, text: str) -> None:
        data = self._check_input(text)
        path = self._path(name, must_exist=True)
        if path.stat().st_size + len(data) > self.max_file_bytes:
            raise WorkspaceError("File size limit exceeded")
        with path.open("ab") as stream:
            stream.write(data)

    def snapshot(self) -> dict[str, str]:
        files = {}
        for name in self.list():
            try:
                files[name] = (self.root / name).read_text(encoding="utf-8")
            except UnicodeDecodeError:
                files[name] = "<binary>"
        return files
