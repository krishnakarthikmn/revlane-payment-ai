"""The storage interface: named blobs, kept apart per tenant."""

from typing import Protocol
from uuid import UUID


class StorageError(Exception):
    pass


class ObjectNotFound(StorageError):
    pass


class Storage(Protocol):
    def put(self, tenant_id: UUID, key: str, content: bytes) -> str:
        """Store `content` under the tenant's `key`. Returns the reference to keep."""

    def get(self, tenant_id: UUID, key: str) -> bytes:
        """The content stored under the tenant's `key`."""
