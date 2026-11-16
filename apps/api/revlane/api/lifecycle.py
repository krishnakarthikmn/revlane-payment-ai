"""Audit-log export and shopper erasure."""

from collections.abc import Iterator
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from revlane import lifecycle
from revlane.api.deps import ADMINISTER, ExistingTenant
from revlane.db.session import tenant_session

router = APIRouter()


@router.get("/audit-log/export", dependencies=ADMINISTER)
def export(tenant_id: ExistingTenant) -> StreamingResponse:
    """The whole audit log as a CSV file. It holds hashes of payloads, never the payloads."""

    def rows(tenant: UUID) -> Iterator[str]:
        with tenant_session(tenant) as session:
            yield from lifecycle.export_audit_log(session, tenant)

    return StreamingResponse(
        rows(tenant_id),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="revlane-audit-log.csv"'},
    )


class Erasure(BaseModel):
    """One of: the shopper's token, or the phone or email on the erasure request."""

    model_config = ConfigDict(extra="forbid")

    customer_token: str | None = Field(default=None, min_length=16, max_length=128)
    phone: str | None = Field(default=None, max_length=30)
    email: str | None = Field(default=None, max_length=200)


@router.post("/privacy/erasures", dependencies=ADMINISTER)
def erase(body: Erasure, tenant_id: ExistingTenant) -> dict[str, Any]:
    """Remove a shopper's token and with it every link between their records.

    The phone or email is used to work out the token and is not kept.
    """
    try:
        with tenant_session(tenant_id) as session:
            erased = lifecycle.erase_shopper(
                session,
                tenant_id,
                customer_token=body.customer_token,
                phone=body.phone,
                email=body.email,
            )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return {"erased": erased}
