"""Sandbox sync for the two gateways with a live connector.

Off unless PROVIDER_LIVE_CALLS is true. Credentials go straight to the vault;
the provider account keeps only a reference to them.
"""

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import select

from revlane.config import Settings, get_settings
from revlane.connectors.live import LiveAccess, SandboxTransport, Transport
from revlane.connectors.registry import LIVE_PROVIDERS, connector_for
from revlane.db import models as m
from revlane.db.session import tenant_session
from revlane.ingest.pipeline import IngestReport, ensure_account, ingest
from revlane.vault import CredentialRef, get_vault


def _require_live_provider(provider: str) -> None:
    if provider not in LIVE_PROVIDERS:
        raise KeyError(f"{provider} has no sandbox connector; it is read from files.")


def connect(
    tenant_id: UUID,
    provider: str,
    credentials: dict[str, str],
    *,
    account_ref: str = "default",
    settings: Settings | None = None,
    transport: Transport | None = None,
) -> CredentialRef:
    """Check sandbox credentials and put them in the vault."""
    _require_live_provider(provider)
    settings = settings or get_settings()
    live = LiveAccess(
        enabled=settings.provider_live_calls,
        transport=transport or SandboxTransport(),
        vault=get_vault(settings),
        entered=dict(credentials),
    )
    ref = connector_for(provider, live).authorize(tenant_id)
    with tenant_session(tenant_id) as session:
        account = ensure_account(session, tenant_id, provider, account_ref)
        account.credential_ref = str(ref)
    return ref


def sync(
    tenant_id: UUID,
    provider: str,
    since: date,
    *,
    account_ref: str = "default",
    now: datetime | None = None,
    settings: Settings | None = None,
    transport: Transport | None = None,
) -> IngestReport:
    """Read everything since a date from the provider's sandbox and ingest it."""
    _require_live_provider(provider)
    settings = settings or get_settings()
    live = LiveAccess(
        enabled=settings.provider_live_calls,
        transport=transport or SandboxTransport(),
        vault=get_vault(settings),
    )
    live.require_enabled()
    with tenant_session(tenant_id) as session:
        stored = session.scalar(
            select(m.ProviderAccount.credential_ref).where(
                m.ProviderAccount.tenant_id == tenant_id,
                m.ProviderAccount.provider == provider,
                m.ProviderAccount.account_ref == account_ref,
            )
        )
    if stored is None:
        raise PermissionError(f"No sandbox credentials are stored for {provider}.")
    live.ref = CredentialRef.parse(stored)
    records = connector_for(provider, live).backfill(since)
    return ingest(
        tenant_id, provider, "api", records, account_ref=account_ref, now=now, settings=settings
    )
