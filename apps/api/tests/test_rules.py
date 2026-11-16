"""The rules in CLAUDE.md that must never be broken: tests for the ones not
already pinned elsewhere, and a check that the security checklist names a
real test for every rule.

The checklist is docs/security-checklist.md.
"""

import ast
import re
from pathlib import Path

import pytest
from sqlalchemy import BigInteger, Float

from revlane.connectors.base import BaseConnector, NotSupported, minor
from revlane.connectors.registry import PROVIDERS, connector_for
from revlane.db import models as m
from revlane.db.base import Base
from revlane.db.session import get_engine
from revlane.harness import actions
from revlane.harness.playbook import ACTION_TYPES, load_all

REPO = Path(__file__).resolve().parents[3]
PACKAGE = REPO / "apps" / "api" / "revlane"
CHECKLIST = REPO / "docs" / "security-checklist.md"
RULES = range(1, 11)


# --- Rule 1: money is integer minor units -------------------------------------------------


def test_every_money_column_is_whole_paise_and_no_column_is_a_float() -> None:
    money, floats = [], []
    for table in Base.metadata.sorted_tables:
        for column in table.columns:
            if column.name.endswith("_minor"):
                money.append((table.name, column.name, isinstance(column.type, BigInteger)))
            if isinstance(column.type, Float):
                floats.append(f"{table.name}.{column.name}")

    assert len(money) > 10
    assert [f"{table}.{name}" for table, name, whole in money if not whole] == []
    assert floats == []


def test_a_float_is_never_accepted_as_an_amount() -> None:
    for bad in (10.5, 0.1 + 0.2, True):
        with pytest.raises((TypeError, ValueError)):
            minor(bad)


# --- Rule 5: nothing is sent to a provider or a customer ----------------------------------

# The only modules that may open a network connection, and why.
MAY_REACH_THE_NETWORK = {
    "connectors/live.py": "sandbox reads, behind PROVIDER_LIVE_CALLS, to two allow-listed hosts",
    "llm/anthropic.py": "the model provider",
    "chat/live.py": "the model provider",
}
NETWORK_MODULES = (
    "urllib.request",
    "http.client",
    "smtplib",
    "socket",
    "ssl",
    "ftplib",
    "httpx",
    "httpx2",
    "requests",
    "aiohttp",
    "anthropic",
    "boto3",
    "twilio",
    "sendgrid",
)


def test_only_three_modules_can_open_a_network_connection() -> None:
    found = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        imported = set()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                imported |= {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                imported.add(node.module or "")
        if any(
            name == mod or name.startswith(f"{mod}.")
            for name in imported
            for mod in NETWORK_MODULES
        ):
            found[str(path.relative_to(PACKAGE))] = True

    assert set(found) == set(MAY_REACH_THE_NETWORK)


def test_no_connector_can_write_to_a_provider() -> None:
    for provider in PROVIDERS:
        connector = connector_for(provider)
        assert isinstance(connector, BaseConnector)
        assert connector.actions() == []
        with pytest.raises(NotSupported):
            connector.execute({"type": "refund"})


def test_every_action_only_drafts_a_document() -> None:
    assert set(actions.DRAFTERS) == set(ACTION_TYPES)
    assert all(name.startswith("draft_") for name in ACTION_TYPES)
    for playbook in load_all().values():
        assert {rule.approval for rule in playbook.actions} == {"required"}
    # Nothing in the harness sends: no mail, no message, no webhook out.
    source = "\n".join(p.read_text(encoding="utf-8") for p in (PACKAGE / "harness").glob("*.py"))
    for word in ("smtplib", "sendmail", "send_message(", "requests.post", "urlopen("):
        assert word not in source


# --- Rule 8: no secrets in code, logs, prompts or fixtures --------------------------------

SECRET_SHAPES = {
    "a live Razorpay key": re.compile(r"rzp_live_[A-Za-z0-9]{6,}"),
    "an Anthropic key": re.compile(r"sk-ant-[A-Za-z0-9_-]{12,}"),
    "an AWS access key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "a private key": re.compile(r"-----BEGIN (RSA |EC |OPENSSH |)PRIVATE KEY-----"),
    "a Slack token": re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    "a GitHub token": re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),
}
SKIP_DIRS = {
    "node_modules",
    ".next",
    ".next-e2e",
    ".git",
    "data",
    ".venv",
    "__pycache__",
    "test-results",
    "playwright-report",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
}
CODE = {".py", ".ts", ".tsx", ".js", ".mjs", ".json", ".html", ".css", ".sh", ".tf"}
TEXT_SUFFIXES = CODE | {".md", ".yaml", ".yml", ".toml", ".example", ".ini", ""}
# Made-up keys that tests use to prove a live key is refused.
DELIBERATE = {("apps/api/tests/test_live_connectors.py", "a live Razorpay key")}


def repository_files() -> list[Path]:
    files = []
    for path in REPO.rglob("*"):
        if not path.is_file() or SKIP_DIRS & set(path.relative_to(REPO).parts):
            continue
        if path.name in (".env", ".make.local") or path.suffix not in TEXT_SUFFIXES:
            continue
        if path.stat().st_size < 2_000_000:
            files.append(path)
    return files


def test_no_file_in_the_repository_holds_something_shaped_like_a_secret() -> None:
    files = repository_files()
    found = []
    for path in files:
        text = path.read_text(encoding="utf-8", errors="ignore")
        found += [
            f"{path.relative_to(REPO)}: {what}"
            for what, shape in SECRET_SHAPES.items()
            if shape.search(text) and (str(path.relative_to(REPO)), what) not in DELIBERATE
        ]

    assert len(files) > 150
    assert found == []


def test_env_files_are_never_committed_and_the_example_holds_no_values() -> None:
    ignored = (REPO / ".gitignore").read_text(encoding="utf-8").splitlines()
    example = (REPO / ".env.example").read_text(encoding="utf-8")

    assert ".env" in ignored and "!.env.example" in ignored
    for name in ("LLM_API_KEY", "RAZORPAY_SANDBOX_KEY_SECRET", "PAYU_SANDBOX_SALT"):
        assert re.search(rf"^{name}=$", example, re.MULTILINE), name


def test_bound_values_never_appear_in_database_errors_or_logs() -> None:
    assert get_engine().hide_parameters is True


# --- Rule 9: status is derived, never stored as the only truth ----------------------------


def test_an_attempt_has_no_status_column_of_its_own() -> None:
    columns = {column.name for column in m.PaymentAttempt.__table__.columns}

    assert not {name for name in columns if "status" in name}
    # The derived status lives apart, names the rule version, and goes with its attempt.
    derived = Base.metadata.tables["attempt_status"]
    assert {"status", "rule_version", "derived_at"} <= {column.name for column in derived.columns}
    assert [
        fk.ondelete
        for fk in derived.foreign_key_constraints
        if fk.referred_table.name == "payment_attempt"
    ] == ["CASCADE"]


# --- The checklist ------------------------------------------------------------------------


def checklist() -> dict[int, str]:
    """Each rule's section of the checklist, by number."""
    text = CHECKLIST.read_text(encoding="utf-8")
    parts = re.split(r"^## Rule (\d+):", text, flags=re.MULTILINE)
    return {int(number): body for number, body in zip(parts[1::2], parts[2::2], strict=True)}


def test_every_rule_in_claude_md_is_in_the_checklist() -> None:
    rules = re.findall(
        r"^(\d+)\. \*\*", (REPO / "CLAUDE.md").read_text(encoding="utf-8"), re.MULTILINE
    )

    assert [int(number) for number in rules] == list(RULES)
    assert sorted(checklist()) == list(RULES)


@pytest.mark.parametrize("rule", RULES)
def test_every_rule_names_a_test_that_exists_or_says_why_not(rule: int) -> None:
    section = checklist()[rule]
    named = re.findall(r"`(apps/[\w/.-]+\.(?:py|ts))::(\w+)`", section)

    assert named or "No automated test:" in section, f"rule {rule} names no test"
    for path, test in named:
        source = (REPO / path).read_text(encoding="utf-8")
        assert re.search(rf"def {test}\b", source) or f'"{test}"' in source, f"{path}::{test}"
