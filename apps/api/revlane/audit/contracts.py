"""Fee contracts: what the merchant agreed with each provider.

A person enters them in a form (or loads a file of them); the fee check reads
them. Rates are basis points and amounts are paise, both whole numbers.
"""

import json
from datetime import date
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    field_validator,
    model_validator,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from revlane.db import models as m
from revlane.db.session import tenant_session
from revlane.ingest.pipeline import ensure_account

Bps = Annotated[StrictInt, Field(ge=0, le=10_000)]
Word = Annotated[str, Field(min_length=1, max_length=50)]


class ContractNotFound(LookupError):
    pass


class ContractOverlap(ValueError):
    """Two rows for the same method and card cannot be in force on the same day."""


class ContractIn(BaseModel):
    """One contract row as a person enters it."""

    model_config = ConfigDict(extra="forbid")

    provider: Word
    account_ref: Word = "default"
    method: Word
    card_network: Word | None = None
    card_type: Word | None = None
    rate_bps: Bps
    fixed_fee_minor: Annotated[StrictInt, Field(ge=0)] = 0
    tax_rate_bps: Bps = 1_800
    # True when the provider may keep the tax on a fee it hands back.
    tax_on_refunded_fee: StrictBool = False
    allowed_fee_types: list[Word] = Field(default_factory=lambda: ["payment"], max_length=20)
    effective_from: date
    effective_to: date | None = None

    @field_validator("provider", "method", "card_network", "card_type", mode="before")
    @classmethod
    def tidy(cls, value: object) -> object:
        """Lower case, as the mappers write methods and networks; blank means none."""
        if isinstance(value, str):
            return value.strip().lower() or None
        return value

    @field_validator("allowed_fee_types")
    @classmethod
    def tidy_types(cls, value: list[str]) -> list[str]:
        return sorted({item.strip().lower() for item in value if item.strip()})

    @model_validator(mode="after")
    def dates_in_order(self) -> "ContractIn":
        if self.provider not in m.PROVIDERS:
            raise ValueError(f"No provider called {self.provider}.")
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError("effective_to is before effective_from.")
        return self


def as_dict(contract: m.FeeContract, account: m.ProviderAccount) -> dict[str, Any]:
    return {
        "id": str(contract.id),
        "provider": account.provider,
        "account_ref": account.account_ref,
        "method": contract.method,
        "card_network": contract.card_network,
        "card_type": contract.card_type,
        "rate_bps": contract.rate_bps,
        "fixed_fee_minor": contract.fixed_fee_minor,
        "tax_rate_bps": contract.tax_rate_bps,
        "tax_on_refunded_fee": contract.tax_on_refunded_fee,
        "allowed_fee_types": list(contract.allowed_fee_types),
        "effective_from": contract.effective_from.isoformat(),
        "effective_to": contract.effective_to.isoformat() if contract.effective_to else None,
    }


def _rows(session: Session, tenant_id: UUID) -> list[tuple[m.FeeContract, m.ProviderAccount]]:
    c, account = m.FeeContract, m.ProviderAccount
    return [
        (contract, owner)
        for contract, owner in session.execute(
            select(c, account)
            .join(
                account, (account.tenant_id == c.tenant_id) & (account.id == c.provider_account_id)
            )
            .where(c.tenant_id == tenant_id)
            .order_by(
                account.provider,
                account.account_ref,
                c.method,
                c.card_network.nulls_first(),
                c.card_type.nulls_first(),
                c.effective_from,
            )
        )
    ]


def list_contracts(session: Session, tenant_id: UUID) -> list[dict[str, Any]]:
    return [as_dict(contract, account) for contract, account in _rows(session, tenant_id)]


def _overlaps(row: m.FeeContract, data: ContractIn) -> bool:
    starts_before_other_ends = row.effective_to is None or data.effective_from <= row.effective_to
    other_starts_before_end = data.effective_to is None or row.effective_from <= data.effective_to
    return starts_before_other_ends and other_starts_before_end


def _check_overlap(
    session: Session,
    tenant_id: UUID,
    account: m.ProviderAccount,
    data: ContractIn,
    own: UUID | None,
) -> None:
    c = m.FeeContract
    same_card = session.scalars(
        select(c).where(
            c.tenant_id == tenant_id,
            c.provider_account_id == account.id,
            c.method == data.method,
            c.card_network.is_not_distinct_from(data.card_network),
            c.card_type.is_not_distinct_from(data.card_type),
        )
    )
    for row in same_card:
        if row.id != own and _overlaps(row, data):
            until = row.effective_to.isoformat() if row.effective_to else "with no end date"
            raise ContractOverlap(
                f"Another {data.provider} {data.method} contract is already in force from "
                f"{row.effective_from.isoformat()} until {until}. End that one first."
            )


def _fill(contract: m.FeeContract, account: m.ProviderAccount, data: ContractIn) -> None:
    contract.provider_account_id = account.id
    contract.method = data.method
    contract.card_network = data.card_network
    contract.card_type = data.card_type
    contract.rate_bps = data.rate_bps
    contract.fixed_fee_minor = data.fixed_fee_minor
    contract.tax_rate_bps = data.tax_rate_bps
    contract.tax_on_refunded_fee = data.tax_on_refunded_fee
    contract.allowed_fee_types = data.allowed_fee_types
    contract.effective_from = data.effective_from
    contract.effective_to = data.effective_to


def create_contract(session: Session, tenant_id: UUID, data: ContractIn) -> dict[str, Any]:
    account = ensure_account(session, tenant_id, data.provider, data.account_ref)
    _check_overlap(session, tenant_id, account, data, own=None)
    contract = m.FeeContract(tenant_id=tenant_id)
    _fill(contract, account, data)
    session.add(contract)
    session.flush()
    return as_dict(contract, account)


def _get(session: Session, tenant_id: UUID, contract_id: UUID) -> m.FeeContract:
    contract = session.scalar(
        select(m.FeeContract).where(
            m.FeeContract.tenant_id == tenant_id, m.FeeContract.id == contract_id
        )
    )
    if contract is None:
        raise ContractNotFound(f"No fee contract {contract_id}.")
    return contract


def update_contract(
    session: Session, tenant_id: UUID, contract_id: UUID, data: ContractIn
) -> dict[str, Any]:
    contract = _get(session, tenant_id, contract_id)
    account = ensure_account(session, tenant_id, data.provider, data.account_ref)
    _check_overlap(session, tenant_id, account, data, own=contract.id)
    _fill(contract, account, data)
    session.flush()
    return as_dict(contract, account)


def delete_contract(session: Session, tenant_id: UUID, contract_id: UUID) -> None:
    session.delete(_get(session, tenant_id, contract_id))
    session.flush()


def load_contracts_file(tenant_id: UUID, path: Path) -> dict[str, int]:
    """Load a JSON list of contract rows, such as the synthetic merchant's.

    Safe to repeat: a row with the same account, method, card and start date
    is updated in place.
    """
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError(f"{path.name} is not a list of contract rows.")
    done = {"read": len(rows), "added": 0, "updated": 0}
    with tenant_session(tenant_id) as session:
        for raw in rows:
            data = ContractIn.model_validate(raw)
            account = ensure_account(session, tenant_id, data.provider, data.account_ref)
            c = m.FeeContract
            same = session.scalar(
                select(c).where(
                    c.tenant_id == tenant_id,
                    c.provider_account_id == account.id,
                    c.method == data.method,
                    c.card_network.is_not_distinct_from(data.card_network),
                    c.card_type.is_not_distinct_from(data.card_type),
                    c.effective_from == data.effective_from,
                )
            )
            if same is None:
                create_contract(session, tenant_id, data)
                done["added"] += 1
            elif as_dict(same, account) != {"id": str(same.id)} | data.model_dump(mode="json"):
                update_contract(session, tenant_id, same.id, data)
                done["updated"] += 1
    return done
