"""The audit log: append-only, and it holds a hash of each payload, never the payload."""

import hashlib
import json
from uuid import UUID

from sqlalchemy.orm import Session

from revlane.db import models as m
from revlane.db.session import exact_json


def digest(payload: object) -> str:
    """A stable hash of anything JSON can hold."""
    canonical = json.dumps(json.loads(exact_json(plain(payload))), sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def plain(value: object) -> object:
    if isinstance(value, dict):
        return {str(key): plain(inner) for key, inner in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [plain(inner) for inner in value]
    if isinstance(value, str | int | bool) or value is None:
        return value
    return str(value)


def record(
    session: Session, tenant_id: UUID, *, actor: str, kind: str, payload: object, ref: str
) -> None:
    session.add(
        m.AuditLog(
            tenant_id=tenant_id, actor=actor, kind=kind, payload_hash=digest(payload), ref=ref
        )
    )
