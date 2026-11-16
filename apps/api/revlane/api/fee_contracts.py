"""Fee contracts: the rates a person enters, which the fee check reads."""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Response

from revlane.api.deps import ADMINISTER, ExistingTenant
from revlane.audit import contracts
from revlane.audit.contracts import ContractIn
from revlane.db.session import tenant_session

router = APIRouter(prefix="/fee-contracts")


@router.get("")
def list_contracts(tenant_id: ExistingTenant) -> dict[str, Any]:
    with tenant_session(tenant_id) as session:
        # A merchant has a few dozen contract rows at most: one page holds them all.
        return {"items": contracts.list_contracts(session, tenant_id), "next_cursor": None}


@router.post("", status_code=201, dependencies=ADMINISTER)
def create(body: ContractIn, tenant_id: ExistingTenant) -> dict[str, Any]:
    try:
        with tenant_session(tenant_id) as session:
            return contracts.create_contract(session, tenant_id, body)
    except contracts.ContractOverlap as error:
        raise HTTPException(409, str(error)) from error


@router.put("/{contract_id}", dependencies=ADMINISTER)
def update(contract_id: UUID, body: ContractIn, tenant_id: ExistingTenant) -> dict[str, Any]:
    try:
        with tenant_session(tenant_id) as session:
            return contracts.update_contract(session, tenant_id, contract_id, body)
    except contracts.ContractNotFound as error:
        raise HTTPException(404, str(error)) from error
    except contracts.ContractOverlap as error:
        raise HTTPException(409, str(error)) from error


@router.delete("/{contract_id}", status_code=204, dependencies=ADMINISTER)
def delete(contract_id: UUID, tenant_id: ExistingTenant) -> Response:
    try:
        with tenant_session(tenant_id) as session:
            contracts.delete_contract(session, tenant_id, contract_id)
    except contracts.ContractNotFound as error:
        raise HTTPException(404, str(error)) from error
    return Response(status_code=204)
