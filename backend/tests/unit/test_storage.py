from pathlib import Path

import pytest

from app.core.storage import LocalStorage, StorageError


async def test_promote_read_and_delete(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    temp = storage.temp_path()
    temp.write_bytes(b"hello")

    await storage.promote(temp, "tenant/doc")

    assert not temp.exists()
    assert await storage.read("tenant/doc") == b"hello"
    await storage.delete("tenant/doc")
    with pytest.raises(StorageError):
        await storage.read("tenant/doc")


async def test_deleting_a_missing_file_is_not_an_error(tmp_path: Path) -> None:
    await LocalStorage(tmp_path).delete("tenant/missing")


@pytest.mark.parametrize("key", ["../outside", "tenant/../../outside", ""])
async def test_keys_cannot_escape_the_storage_folder(tmp_path: Path, key: str) -> None:
    with pytest.raises(StorageError):
        await LocalStorage(tmp_path / "root").read(key)
