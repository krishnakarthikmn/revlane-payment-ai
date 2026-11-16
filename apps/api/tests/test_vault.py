"""The vault interface and its development backend."""

from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text

from revlane.config import Settings
from revlane.db import models as m
from revlane.db.session import admin_session, tenant_session
from revlane.vault import CredentialRef, LocalVault, SecretNotFound, VaultError, get_vault

pytestmark = pytest.mark.usefixtures("database")

SECRET = "rzp_test_not_a_real_key_3f9a"


@pytest.fixture
def tenant(database: None) -> Iterator[UUID]:
    with admin_session() as session:
        row = m.Tenant(id=uuid4(), name="vault test")
        session.add(row)
    yield row.id


@pytest.fixture
def vault() -> LocalVault:
    return LocalVault("test-root-key")


def test_a_secret_comes_back_as_it_went_in(vault: LocalVault, tenant: UUID) -> None:
    ref = vault.put(tenant, "razorpay", SECRET)

    assert ref == CredentialRef(tenant, "razorpay")
    assert vault.get(ref) == SECRET


def test_putting_again_replaces_the_value(vault: LocalVault, tenant: UUID) -> None:
    vault.put(tenant, "razorpay", "old")
    ref = vault.put(tenant, "razorpay", "new")

    assert vault.get(ref) == "new"


def test_the_stored_value_is_not_the_secret(vault: LocalVault, tenant: UUID) -> None:
    vault.put(tenant, "razorpay", SECRET)

    with tenant_session(tenant) as session:
        stored = session.scalar(select(m.VaultSecret.ciphertext))
        as_text = session.scalar(text("SELECT encode(ciphertext, 'escape') FROM vault_secret"))

    assert stored is not None
    assert SECRET.encode() not in stored
    assert SECRET not in (as_text or "")


def test_the_reference_and_the_vault_do_not_reveal_anything(
    vault: LocalVault, tenant: UUID
) -> None:
    ref = vault.put(tenant, "razorpay", SECRET)

    assert SECRET not in str(ref)
    assert SECRET not in repr(ref)
    assert "test-root-key" not in repr(vault)
    assert CredentialRef.parse(str(ref)) == ref


def test_a_missing_secret_is_reported(vault: LocalVault, tenant: UUID) -> None:
    with pytest.raises(SecretNotFound):
        vault.get(CredentialRef(tenant, "nothing-here"))


def test_another_tenant_cannot_read_it(vault: LocalVault, tenant: UUID) -> None:
    vault.put(tenant, "razorpay", SECRET)
    with admin_session() as session:
        other = m.Tenant(id=uuid4(), name="someone else")
        session.add(other)

    with pytest.raises(SecretNotFound):
        vault.get(CredentialRef(other.id, "razorpay"))


def test_a_different_root_key_cannot_decrypt(vault: LocalVault, tenant: UUID) -> None:
    ref = vault.put(tenant, "razorpay", SECRET)

    with pytest.raises(VaultError) as failure:
        LocalVault("some-other-key").get(ref)

    assert not isinstance(failure.value, SecretNotFound)
    assert SECRET not in str(failure.value)
    assert failure.value.__cause__ is None


def test_delete_removes_one_secret_and_is_safe_to_repeat(vault: LocalVault, tenant: UUID) -> None:
    ref = vault.put(tenant, "razorpay", SECRET)
    keep = vault.put(tenant, "payu", "kept")

    vault.delete(ref)
    vault.delete(ref)

    with pytest.raises(SecretNotFound):
        vault.get(ref)
    assert vault.get(keep) == "kept"


def test_offboarding_removes_every_secret_of_one_tenant(vault: LocalVault, tenant: UUID) -> None:
    vault.put(tenant, "razorpay", SECRET)
    vault.put(tenant, "payu", "x")

    assert vault.delete_all(tenant) == 2
    assert vault.delete_all(tenant) == 0


def test_an_empty_root_key_is_refused() -> None:
    with pytest.raises(VaultError):
        LocalVault("")


def test_get_vault_picks_the_backend_from_settings() -> None:
    local = Settings(_env_file=None, vault_backend="local", vault_key="k")
    unknown = Settings(_env_file=None, vault_backend="hsm", vault_key="k")

    assert isinstance(get_vault(local), LocalVault)
    with pytest.raises(VaultError):
        get_vault(unknown)


def test_a_reference_must_be_well_formed() -> None:
    for bad in ("razorpay", "vault://not-a-uuid/razorpay", "vault://"):
        with pytest.raises(VaultError):
            CredentialRef.parse(bad)
