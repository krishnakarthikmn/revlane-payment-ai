"""`make demo`: a small synthetic merchant, loaded and audited, ready to look at.

Does nothing to a demo tenant that already holds data, so it is safe to run
again and safe beside a full-size load.
"""

import argparse
import tempfile
from datetime import datetime, time
from pathlib import Path

from sqlalchemy import func, select

from revlane.audit.contracts import load_contracts_file
from revlane.bootstrap import ensure_demo_users, ensure_tenant
from revlane.config import get_settings
from revlane.connectors.base import IST
from revlane.db import models as m
from revlane.db.session import tenant_session
from revlane.harness import queue
from revlane.ingest.pipeline import ingest_directory
from revlane.llm.stub import StubClient
from revlane.synth import config as synth
from revlane.synth.generate import generate

PLAYBOOKS = ("leakage_audit", "reconciliation", "fee_audit")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m revlane.demo", description=__doc__)
    parser.add_argument("--scale", type=float, default=0.05, help="share of full volume")
    args = parser.parse_args(argv)

    settings = get_settings()
    if not settings.token_hash_root_key or not settings.vault_key:
        print("TOKEN_HASH_ROOT_KEY and VAULT_KEY are not set. Copy .env.example to .env first.")
        return 1
    tenant_id = settings.demo_tenant_id
    ensure_tenant(tenant_id, settings.demo_tenant_name)
    ensure_demo_users(tenant_id, settings)
    with tenant_session(tenant_id) as session:
        loaded = session.scalar(select(func.count()).select_from(m.PaymentAttempt)) or 0
    if loaded:
        print(
            f"{settings.demo_tenant_name} already holds {loaded:,} payments: leaving it as it is."
        )
        return 0

    today = datetime.now(tz=IST).date()
    print(f"Generating the synthetic merchant at {args.scale:g} of full size, dated {today}…")
    with tempfile.TemporaryDirectory() as folder:
        path = generate(
            seed=synth.DEFAULT_SEED, as_of=today, scale=args.scale, out_root=Path(folder)
        ).path
        print("Loading its files…")
        reports = ingest_directory(
            tenant_id, path, now=datetime.combine(today, time.max, tzinfo=IST)
        )
        load_contracts_file(tenant_id, path / "contracts" / "fee_contracts.json")
    read = sum(report.read for report in reports)
    print(f"Read {read:,} records from {len(reports)} files. Running the audit…")
    # Always the stub here: the demo needs no key and sends nothing anywhere.
    for playbook in PLAYBOOKS:
        done = queue.run_now(tenant_id, playbook, StubClient(), as_of=today)
        headline = ((done.output or {}).get("rendered") or {}).get("headline", done.error)
        print(f"  {playbook}: {done.status}. {headline}")
    print("Ready. Starting the API, the worker and the console…")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
