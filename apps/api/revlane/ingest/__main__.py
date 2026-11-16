"""`make ingest`: load a folder of provider files, such as the synthetic merchant."""

import argparse
import sys
from datetime import date, datetime, time
from pathlib import Path

from revlane.audit.contracts import load_contracts_file
from revlane.bootstrap import ensure_demo_users, ensure_tenant
from revlane.config import get_settings
from revlane.connectors.base import IST
from revlane.ingest.health import connector_health
from revlane.ingest.pipeline import ingest_directory
from revlane.synth import config as synth
from revlane.synth.generate import DEFAULT_OUT


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m revlane.ingest", description=__doc__)
    parser.add_argument(
        "folder",
        nargs="?",
        type=Path,
        default=DEFAULT_OUT / str(synth.DEFAULT_SEED),
        help="a folder laid out as <provider>/<file> (default: the synthetic merchant)",
    )
    parser.add_argument(
        "--as-of",
        type=date.fromisoformat,
        default=None,
        help="treat this date as today when applying clock rules (default: now)",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    if not args.folder.is_dir():
        print(f"No folder {args.folder}. Run `make synth` first.", file=sys.stderr)
        return 1
    if not settings.token_hash_root_key:
        print("TOKEN_HASH_ROOT_KEY is not set. Copy .env.example to .env.", file=sys.stderr)
        return 1
    now = datetime.combine(args.as_of, time.max, tzinfo=IST) if args.as_of else None

    ensure_tenant(settings.demo_tenant_id, settings.demo_tenant_name)
    ensure_demo_users(settings.demo_tenant_id, settings)
    print(f"Ingesting {args.folder} for {settings.demo_tenant_name}")
    for report in ingest_directory(settings.demo_tenant_id, args.folder, now=now):
        added = ", ".join(f"{count:,} {name}" for name, count in report.added.items() if count)
        print(f"  {report.provider}/{report.source}: read {report.read:,}; new: {added or 'none'}")
        if report.rejected:
            print(f"    rejected {report.rejected:,}: {'; '.join(report.rejections)}")

    contracts = args.folder / "contracts" / "fee_contracts.json"
    if contracts.is_file():
        loaded = load_contracts_file(settings.demo_tenant_id, contracts)
        print(
            f"  contracts/{contracts.name}: read {loaded['read']:,}; "
            f"new: {loaded['added']:,} fee_contract; updated: {loaded['updated']:,}"
        )

    health = connector_health(settings.demo_tenant_id)
    print("Connector health")
    for connector in health["connectors"]:
        records = ", ".join(f"{count:,} {name}" for name, count in connector["records"].items())
        print(f"  {connector['provider']}: {records}")
    if health["alerts"]:
        print("Alerts")
    for alert in health["alerts"]:
        print(
            f"  {alert['provider']} status {alert['subject']!r} is not mapped: "
            f"{alert['record_count']} attempts"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
