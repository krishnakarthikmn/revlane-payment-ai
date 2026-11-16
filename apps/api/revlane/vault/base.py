"""The vault interface."""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID


class VaultError(Exception):
    """A vault operation failed. The message never contains a secret."""


class SecretNotFound(VaultError):
    pass


@dataclass(frozen=True)
class CredentialRef:
    """Points at a secret without containing it. Safe to store and to log."""

    tenant_id: UUID
    name: str

    def __str__(self) -> str:
        return f"vault://{self.tenant_id}/{self.name}"

    @classmethod
    def parse(cls, value: str) -> "CredentialRef":
        prefix = "vault://"
        tenant, _, name = value.removeprefix(prefix).partition("/")
        if not value.startswith(prefix) or not name:
            raise VaultError("Not a credential reference")
        try:
            return cls(UUID(tenant), name)
        except ValueError as error:
            raise VaultError("Not a credential reference") from error


class Vault(Protocol):
    def put(self, tenant_id: UUID, name: str, secret: str) -> CredentialRef:
        """Store a secret, replacing any earlier value under the same name."""
        ...

    def get(self, ref: CredentialRef) -> str:
        """Return the secret. Raises SecretNotFound if there is none."""
        ...

    def delete(self, ref: CredentialRef) -> None:
        """Remove one secret. Removing a missing secret is not an error."""
        ...

    def delete_all(self, tenant_id: UUID) -> int:
        """Remove every secret a tenant has (offboarding). Returns how many."""
        ...
