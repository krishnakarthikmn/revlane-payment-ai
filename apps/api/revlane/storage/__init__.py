"""Object storage behind a small interface. Development keeps files in a local folder."""

from functools import lru_cache

from revlane.config import get_settings
from revlane.storage.base import Storage, StorageError
from revlane.storage.local import LocalStorage

__all__ = ["Storage", "StorageError", "get_storage"]


@lru_cache
def get_storage() -> Storage:
    settings = get_settings()
    if settings.storage_backend != "local":
        raise StorageError(f"No storage backend called {settings.storage_backend}.")
    return LocalStorage(settings.storage_root)
