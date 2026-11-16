"""Generates the synthetic merchant into `data/synth/<seed>/`."""

import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from revlane.config import REPO_ROOT
from revlane.synth import config as c
from revlane.synth.render import render
from revlane.synth.world import World, build_world

DEFAULT_OUT = REPO_ROOT / "data" / "synth"
ASSUMPTIONS = Path(__file__).with_name("ASSUMPTIONS.md")


@dataclass(frozen=True)
class Result:
    path: Path
    files: dict[str, str]  # relative path -> sha256
    answer_key: dict[str, Any]
    attempts: int


def answer_key(world: World) -> dict[str, Any]:
    cal = world.cal
    return {
        "seed": world.seed,
        "generated_on": cal.as_of.isoformat(),
        "scale": str(world.scale),
        "period": {"start": cal.days[0].isoformat(), "end": cal.days[-1].isoformat()},
        "fee_month": cal.fee_month,
        "leaks": [
            {
                "id": leak.id,
                "type": leak.type,
                "provider": leak.provider,
                "record_ids": leak.records,
                "amount_minor": leak.amount,
                "expected_state": "recoverable",
                "items": [{"id": item, "amount_minor": amount} for item, amount in leak.items],
            }
            for leak in world.leaks
        ],
        "totals_minor": {
            "reconciliation": c.RECONCILIATION_TOTAL,
            "fee_audit": c.FEE_AUDIT_TOTAL,
        },
        "fee_audit_lines": c.FEE_AUDIT_LINES,
        "traps": [
            {"id": trap.id, "record_ids": trap.records, "expect": trap.expect}
            for trap in world.traps
        ],
    }


def generate(
    seed: int = c.DEFAULT_SEED,
    as_of: date | None = None,
    scale: float = 1.0,
    out_root: Path = DEFAULT_OUT,
) -> Result:
    """Build the merchant and write it. The same seed, date and scale always
    write the same bytes."""
    if scale <= 0:
        raise ValueError("scale must be positive")
    world = build_world(seed, c.calendar_for(as_of or date.today()), scale)
    key = answer_key(world)

    out = out_root / str(seed)
    if out.exists():
        shutil.rmtree(out)
    hashes: dict[str, str] = {}

    def write(relative: str, contents: str) -> None:
        target = out / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        data = contents.encode("utf-8")
        target.write_bytes(data)
        hashes[relative] = hashlib.sha256(data).hexdigest()

    for relative, contents in render(world):
        write(relative, contents)
    write("answer_key.json", json.dumps(key, indent=2) + "\n")
    shutil.copyfile(ASSUMPTIONS, out_root / ASSUMPTIONS.name)
    return Result(out, hashes, key, len(world.attempts))
