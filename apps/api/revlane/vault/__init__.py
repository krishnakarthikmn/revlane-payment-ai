"""Where credentials live. Nothing else in Revlane stores a secret."""

from revlane.config import Settings, get_settings
from revlane.vault.base import CredentialRef, SecretNotFound, Vault, VaultError
from revlane.vault.local import LocalVault

__all__ = ["CredentialRef", "LocalVault", "SecretNotFound", "Vault", "VaultError", "get_vault"]


def get_vault(settings: Settings | None = None) -> Vault:
    """The vault for this deployment. Only the development backend exists so far;
    production uses a KMS-backed secrets manager behind the same interface."""
    settings = settings or get_settings()
    if settings.vault_backend == "local":
        return LocalVault(settings.vault_key)
    raise VaultError(f"Unknown vault backend: {settings.vault_backend}")
