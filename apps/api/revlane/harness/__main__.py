"""`make agent` and `make worker`: run a playbook now, or work the queue."""

import argparse
import os
import platform
import time
from datetime import date

from revlane.config import get_settings
from revlane.db import models as m
from revlane.harness import playbook, queue
from revlane.llm.client import get_client

POLL_SECONDS = 2.0


def show(run: m.AgentRun) -> None:
    print(f"{run.playbook}: {run.status}" + (f" ({run.error})" if run.error else ""))
    output = run.output or {}
    rendered = output.get("rendered") or {}
    if rendered.get("headline"):
        print(f"  {rendered['headline']}")
    for point in rendered.get("points", []):
        print(f"  - {point}")
    if output.get("proposal_ids"):
        print(f"  proposals waiting for approval: {len(output['proposal_ids'])}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m revlane.harness", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="run one playbook for the demo tenant, now")
    run.add_argument("playbook", choices=sorted(playbook.load_all()))
    run.add_argument("--as-of", type=date.fromisoformat, default=None)
    worker = commands.add_parser("worker", help="carry out queued runs")
    worker.add_argument("--once", action="store_true", help="stop when the queue is empty")
    args = parser.parse_args(argv)

    model = get_client()
    if args.command == "run":
        done = queue.run_now(get_settings().demo_tenant_id, args.playbook, model, as_of=args.as_of)
        show(done)
        return 0 if done.status in ("succeeded", "escalated", "skipped") else 1

    name = f"{platform.node()}:{os.getpid()}"
    print(f"Worker {name} is waiting for runs ({model.name} model).")
    while True:
        done_run = queue.work_once(name, model)
        if done_run is not None:
            show(done_run)
        elif args.once:
            return 0
        else:
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    raise SystemExit(main())
