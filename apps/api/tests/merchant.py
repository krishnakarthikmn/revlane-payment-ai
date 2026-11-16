"""The synthetic merchant, loaded into a tenant of its own, for tests and evals."""

from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from revlane.audit.contracts import load_contracts_file
from revlane.bootstrap import ensure_tenant
from revlane.connectors.base import IST
from revlane.ingest.pipeline import ingest_directory
from revlane.synth.generate import generate

AS_OF = date(2026, 11, 16)
SCALE = 0.02


@dataclass
class Merchant:
    tenant_id: UUID
    path: Path
    key: dict[str, Any]

    def leak(self, leak_id: str) -> dict[str, Any]:
        found: dict[str, Any] = next(leak for leak in self.key["leaks"] if leak["id"] == leak_id)
        return found


def load_merchant(folder: Path, name: str, seed: int = 42) -> Merchant:
    """Generate the merchant, ingest every file and load its contracts."""
    result = generate(seed=seed, as_of=AS_OF, scale=SCALE, out_root=folder)
    return load_files(result.path, name, result.answer_key)


def load_files(path: Path, name: str, key: dict[str, Any]) -> Merchant:
    """Ingest a folder of the merchant's files into a new tenant."""
    tenant_id = uuid4()
    ensure_tenant(tenant_id, name)
    ingest_directory(tenant_id, path, now=datetime.combine(AS_OF, time.max, tzinfo=IST))
    load_contracts_file(tenant_id, path / "contracts" / "fee_contracts.json")
    return Merchant(tenant_id, path, key)
