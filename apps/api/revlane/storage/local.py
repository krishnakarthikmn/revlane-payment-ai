"""Development storage: one folder per tenant under a root directory."""

from pathlib import Path, PurePosixPath
from uuid import UUID

from revlane.storage.base import ObjectNotFound, StorageError


class LocalStorage:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, tenant_id: UUID, key: str) -> Path:
        parts = PurePosixPath(key).parts
        if not parts or key.startswith("/") or any(part in ("..", ".") for part in parts):
            raise StorageError("Not a storage key.")
        return self.root.joinpath(str(tenant_id), *parts)

    def put(self, tenant_id: UUID, key: str, content: bytes) -> str:
        path = self._path(tenant_id, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return key

    def get(self, tenant_id: UUID, key: str) -> bytes:
        path = self._path(tenant_id, key)
        if not path.is_file():
            raise ObjectNotFound(f"Nothing stored under {key}.")
        return path.read_bytes()
