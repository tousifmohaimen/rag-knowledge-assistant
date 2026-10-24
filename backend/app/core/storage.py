"""File storage for uploaded documents.

Only local disk is implemented. Code depends on the ``Storage`` protocol, so moving to
S3 or another object store later means adding a class, not changing callers.
"""

import asyncio
import os
import uuid
from pathlib import Path
from typing import Protocol


class StorageError(Exception):
    pass


class Storage(Protocol):
    def temp_path(self) -> Path:
        """A fresh local path to write an upload to before it is accepted."""
        ...

    async def promote(self, temp: Path, key: str) -> None:
        """Move an accepted upload from its temp path to its final key."""
        ...

    async def read(self, key: str) -> bytes: ...

    async def delete(self, key: str) -> None: ...

    async def discard(self, temp: Path) -> None: ...


def document_key(tenant_id: uuid.UUID, document_id: uuid.UUID) -> str:
    return f"{tenant_id}/{document_id}"


class LocalStorage:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self._tmp = self.root / ".tmp"

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        # Keys are built from UUIDs, but never trust that: refuse anything that would
        # land outside the storage folder (for example "../../etc/passwd").
        if not path.is_relative_to(self.root) or path == self.root:
            raise StorageError(f"invalid storage key: {key!r}")
        return path

    def temp_path(self) -> Path:
        self._tmp.mkdir(parents=True, exist_ok=True)
        return self._tmp / uuid.uuid4().hex

    async def promote(self, temp: Path, key: str) -> None:
        target = self._path(key)

        def _move() -> None:
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(temp, target)

        await asyncio.to_thread(_move)

    async def read(self, key: str) -> bytes:
        path = self._path(key)
        try:
            return await asyncio.to_thread(path.read_bytes)
        except FileNotFoundError as exc:
            raise StorageError(f"file not found: {key}") from exc

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._path(key).unlink, missing_ok=True)

    async def discard(self, temp: Path) -> None:
        await asyncio.to_thread(temp.unlink, missing_ok=True)
