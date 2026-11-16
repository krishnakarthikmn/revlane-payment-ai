"""Development helpers for the browser tests. Never for a real database.

`python -m revlane.dev reset`       empty the demo tenant
`python -m revlane.dev files DIR`   write the synthetic merchant's files, small, dated today
`python -m revlane.dev seed`        reset, then load the files and run the audit

Every command refuses to run unless the database's name ends in `_e2e`.
"""

import argparse
import sys
import tempfile
from datetime import datetime, time
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, delete, make_url, text
from sqlalchemy.exc import OperationalError, ProgrammingError

from revlane.audit.contracts import load_contracts_file
from revlane.bootstrap import ensure_demo_users, ensure_tenant
from revlane.config import get_settings
from revlane.connectors.base import IST
from revlane.db import models as m
from revlane.db.session import admin_session
from revlane.harness import queue
from revlane.ingest.pipeline import ingest_directory
from revlane.llm.stub import StubClient
from revlane.synth.generate import generate

API_ROOT = Path(__file__).resolve().parents[1]
SCALE = 0.02
SEED = 42


def check_database() -> None:
    url = make_url(get_settings().database_url)
    if not (url.database or "").endswith("_e2e"):
        raise SystemExit(
            f"Refusing to touch the database {url.database!r}: these commands only run "
            "against a database whose name ends in _e2e."
        )
    try:
        with create_engine(url).connect():
            pass
    except OperationalError:
        admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
        try:
            with admin.connect() as connection:
                connection.execute(text(f'CREATE DATABASE "{url.database}"'))
        except (OperationalError, ProgrammingError) as error:
            raise SystemExit(
                f"The database {url.database} does not exist and could not be created. "
                "Run `make db`."
            ) from error
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "migrations"))
    command.upgrade(config, "head")


def reset() -> None:
    """Remove the demo tenant and everything it owns, then create it empty."""
    settings = get_settings()
    with admin_session() as session:
        session.execute(delete(m.Tenant).where(m.Tenant.id == settings.demo_tenant_id))
    ensure_tenant(settings.demo_tenant_id, settings.demo_tenant_name)
    ensure_demo_users(settings.demo_tenant_id, settings)


def files(folder: Path) -> Path:
    today = datetime.now(tz=IST).date()
    return generate(seed=SEED, as_of=today, scale=SCALE, out_root=folder).path


def seed() -> None:
    settings = get_settings()
    tenant_id = settings.demo_tenant_id
    reset()
    today = datetime.now(tz=IST).date()
    with tempfile.TemporaryDirectory() as folder:
        path = files(Path(folder))
        ingest_directory(tenant_id, path, now=datetime.combine(today, time.max, tzinfo=IST))
        load_contracts_file(tenant_id, path / "contracts" / "fee_contracts.json")
    for playbook in ("leakage_audit", "reconciliation", "fee_audit"):
        done = queue.run_now(tenant_id, playbook, StubClient(), as_of=today)
        if done.status != "succeeded":
            raise SystemExit(f"{playbook} ended {done.status}: {done.error}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m revlane.dev", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("reset")
    commands.add_parser("seed")
    commands.add_parser("files").add_argument("folder", type=Path)
    args = parser.parse_args(argv)

    check_database()
    if args.command == "reset":
        reset()
    elif args.command == "seed":
        seed()
    else:
        print(files(args.folder))
    return 0


if __name__ == "__main__":
    sys.exit(main())
