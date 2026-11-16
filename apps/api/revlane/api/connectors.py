"""Connector endpoints: file upload, health, and sandbox credentials and sync."""

from datetime import date
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from revlane.api.deps import ACT, ADMINISTER, NO_TENANT, Tenant
from revlane.connectors.live import LiveCallsDisabled, ProviderError
from revlane.connectors.registry import (
    LIVE_PROVIDERS,
    PROVIDERS,
    UnknownFile,
    detect_provider,
)
from revlane.ingest import live
from revlane.ingest.health import connector_health
from revlane.ingest.pipeline import TenantNotFound, ingest_text

router = APIRouter(prefix="/connectors")

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
# What a browser or a script calls a CSV or JSON-lines file. Anything else is refused.
UPLOAD_TYPES = frozenset(
    {
        "",
        "text/csv",
        "text/plain",
        "application/csv",
        "application/json",
        "application/jsonl",
        "application/x-ndjson",
        "application/vnd.ms-excel",
        "application/octet-stream",
    }
)


def known(provider: str, among: tuple[str, ...] = PROVIDERS) -> str:
    if provider not in among:
        raise HTTPException(404, f"No connector for {provider}.")
    return provider


@router.get("/health")
def health(tenant_id: Tenant) -> dict[str, Any]:
    return connector_health(tenant_id)


async def _read_upload(request: Request) -> list[str]:
    """The request body as the lines of a UTF-8 text file, within the size limit."""
    kind = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if kind not in UPLOAD_TYPES:
        raise HTTPException(
            415, f"A {kind} file cannot be read. Upload a CSV or JSON-lines export."
        )
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "The file is larger than 25 MB.")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "The file is larger than 25 MB.")
    try:
        text = bytes(body).decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise HTTPException(422, "The file is not UTF-8 text.") from error
    if "\x00" in text:
        raise HTTPException(415, "That is not a text file. Upload a CSV or JSON-lines export.")
    return text.splitlines(keepends=True)


async def _ingest(
    tenant_id: UUID, provider: str, filename: str, account_ref: str, lines: list[str]
) -> dict[str, Any]:
    def run() -> dict[str, Any]:
        return ingest_text(
            tenant_id, provider, filename, lines, account_ref=account_ref, actor="upload"
        ).as_dict()

    try:
        return await run_in_threadpool(run)
    except UnknownFile as error:
        raise HTTPException(422, str(error)) from error
    except TenantNotFound as error:
        raise HTTPException(404, NO_TENANT) from error


Filename = Annotated[str, Query(min_length=1, max_length=200)]
AccountRef = Annotated[str, Query(min_length=1, max_length=100)]


@router.post("/uploads", dependencies=ACT)
async def upload_any(
    request: Request, tenant_id: Tenant, filename: Filename, account_ref: AccountRef = "default"
) -> dict[str, Any]:
    """Upload a file without saying whose it is: the first record decides."""
    lines = await _read_upload(request)
    try:
        provider = detect_provider(filename, lines)
    except UnknownFile as error:
        raise HTTPException(422, str(error)) from error
    return await _ingest(tenant_id, provider, filename, account_ref, lines)


@router.post("/{provider}/uploads", dependencies=ACT)
async def upload(
    provider: str,
    request: Request,
    tenant_id: Tenant,
    filename: Filename,
    account_ref: AccountRef = "default",
) -> dict[str, Any]:
    """Upload one file as the request body: CSV or JSON lines, UTF-8.

    The file itself is not stored. Its records are, without card numbers or
    contact details.
    """
    known(provider)
    return await _ingest(tenant_id, provider, filename, account_ref, await _read_upload(request))


class Credentials(BaseModel):
    """Sandbox credentials. Razorpay: key_id, key_secret, webhook_secret. PayU: key, salt."""

    values: dict[str, str]


@router.put("/{provider}/credentials", dependencies=ADMINISTER)
def store_credentials(provider: str, body: Credentials, tenant_id: Tenant) -> dict[str, str]:
    known(provider, LIVE_PROVIDERS)
    try:
        live.connect(tenant_id, provider, body.values)
    except LiveCallsDisabled as error:
        raise HTTPException(409, str(error)) from error
    except PermissionError as error:
        raise HTTPException(422, str(error)) from error
    return {"provider": provider, "status": "stored"}


@router.post("/{provider}/sync", dependencies=ADMINISTER)
def sync(provider: str, since: date, tenant_id: Tenant) -> dict[str, Any]:
    known(provider, LIVE_PROVIDERS)
    try:
        return live.sync(tenant_id, provider, since).as_dict()
    except LiveCallsDisabled as error:
        raise HTTPException(409, str(error)) from error
    except PermissionError as error:
        raise HTTPException(422, str(error)) from error
    except ProviderError as error:
        raise HTTPException(502, str(error)) from error
