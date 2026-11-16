"""Rule 4: every query runs in a tenant-scoped session.

Sessions that row-level security does not restrict may only be opened in the
few places listed here. Anything else must use `tenant_session`.
"""

from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "revlane"
UNRESTRICTED = ("get_engine(", "admin_session(", "create_engine(")
ALLOWED = {
    "db/session.py",  # defines them
    "api/health.py",  # SELECT 1, no tables
    "bootstrap.py",  # creates a tenant, which an application session may not
    "harness/queue.py",  # a worker claims the next run across tenants; queue columns only
    "lifecycle.py",  # retention deletes from append-only history, which an app session may not
    "api/metrics.py",  # aggregate counts across tenants, with no tenant named
    "auth/accounts.py",  # finds the account behind an email before anyone is signed in
    "dev.py",  # empties the demo tenant for the browser tests; refuses any non-e2e database
}


def test_unrestricted_database_access_stays_in_the_allowed_files() -> None:
    offenders = sorted(
        str(path.relative_to(PACKAGE))
        for path in PACKAGE.rglob("*.py")
        if str(path.relative_to(PACKAGE)) not in ALLOWED
        and any(call in path.read_text(encoding="utf-8") for call in UNRESTRICTED)
    )

    assert offenders == []
