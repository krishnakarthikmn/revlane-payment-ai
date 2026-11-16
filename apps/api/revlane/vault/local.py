"""Development vault: secrets encrypted with pgcrypto and kept in Postgres.

Each tenant's secrets are encrypted under a key derived from `VAULT_KEY` and
the tenant id, and the table is covered by row-level security like any other.
For development only: in production the vault is a KMS-backed secrets manager.
"""

import hashlib
import hmac
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from revlane.db.session import tenant_session
from revlane.vault.base import CredentialRef, SecretNotFound, VaultError


class LocalVault:
    def __init__(self, root_key: str) -> None:
        if not root_key:
            raise VaultError("VAULT_KEY is not set")
        self._root_key = root_key.encode()

    def __repr__(self) -> str:
        return "LocalVault()"

    def _key(self, tenant_id: UUID) -> str:
        return hmac.new(self._root_key, tenant_id.bytes, hashlib.sha256).hexdigest()

    def put(self, tenant_id: UUID, name: str, secret: str) -> CredentialRef:
        if not name:
            raise VaultError("A secret needs a name")
        with tenant_session(tenant_id) as session:
            session.execute(
                text(
                    "INSERT INTO vault_secret (tenant_id, name, ciphertext) "
                    "VALUES (:tenant_id, :name, pgp_sym_encrypt(:secret, :key)) "
                    "ON CONFLICT (tenant_id, name) DO UPDATE "
                    "SET ciphertext = EXCLUDED.ciphertext, updated_at = now()"
                ),
                {
                    "tenant_id": tenant_id,
                    "name": name,
                    "secret": secret,
                    "key": self._key(tenant_id),
                },
            )
        return CredentialRef(tenant_id, name)

    def get(self, ref: CredentialRef) -> str:
        try:
            with tenant_session(ref.tenant_id) as session:
                row = session.execute(
                    text(
                        "SELECT pgp_sym_decrypt(ciphertext, :key) "
                        "FROM vault_secret WHERE name = :name"
                    ),
                    {"name": ref.name, "key": self._key(ref.tenant_id)},
                ).first()
        except DBAPIError:
            # Deliberately no detail and no chained cause: neither may carry key material.
            raise VaultError("The secret could not be decrypted") from None
        if row is None:
            raise SecretNotFound(str(ref))
        secret: str = row[0]
        return secret

    def delete(self, ref: CredentialRef) -> None:
        with tenant_session(ref.tenant_id) as session:
            session.execute(text("DELETE FROM vault_secret WHERE name = :name"), {"name": ref.name})

    def delete_all(self, tenant_id: UUID) -> int:
        with tenant_session(tenant_id) as session:
            rows = session.execute(text("DELETE FROM vault_secret RETURNING id")).all()
        return len(rows)
