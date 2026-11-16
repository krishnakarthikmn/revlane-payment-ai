"""Milestone 6 acceptance for Ask Revlane.

- No path lets model text reach the database as SQL.
- A cross-tenant question returns nothing from another tenant.
- A question that needs a metric that is not defined is refused.

The question set with SQL ground truth is in evals/test_chat_questions.py.
"""

import ast
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import DBAPIError

from revlane.chat import execute, semantic, service
from revlane.chat.compiler import SpecError, compile_spec
from revlane.chat.models import Plan, StubChat
from revlane.chat.spec import Filter, QuerySpec, resolve
from revlane.connectors.base import IST
from revlane.db import models as m
from revlane.db.session import chat_session, tenant_session
from revlane.harness import grounding, queue
from revlane.llm.stub import StubClient
from revlane.main import create_app
from tests.merchant import AS_OF, Merchant, load_merchant
from tests.signin import sign_in

pytestmark = pytest.mark.usefixtures("database")

LAYER = semantic.load()
# Findings are dated by when they were found, which in a test is today.
TODAY = datetime.now(tz=IST).date()
INJECTION = "x'; DROP TABLE payment_attempt; --"


@pytest.fixture(scope="module")
def merchant(database: None, tmp_path_factory: pytest.TempPathFactory) -> Merchant:
    loaded = load_merchant(tmp_path_factory.mktemp("chat"), "chat")
    queue.run_now(loaded.tenant_id, "leakage_audit", StubClient(), as_of=AS_OF)
    return loaded


@pytest.fixture(scope="module")
def other(database: None, tmp_path_factory: pytest.TempPathFactory) -> Merchant:
    """A second tenant with different data: a different seed."""
    return load_merchant(tmp_path_factory.mktemp("chat-other"), "chat-other", seed=7)


def ask(tenant_id: UUID, question: str, model: Any = None, as_of: date = AS_OF) -> dict[str, Any]:  # noqa: ANN401
    """Every event of one answer, by name. Tables are kept as a list."""
    seen: dict[str, Any] = {"steps": [], "tables": []}
    for item in service.ask(tenant_id, question, model or StubChat(), as_of=as_of):
        if item["event"] == "step":
            seen["steps"].append(item["data"]["name"])
        elif item["event"] == "table":
            seen["tables"].append(item["data"])
        else:
            seen[item["event"]] = item["data"]
    return seen


def run(tenant_id: UUID, spec: QuerySpec, as_of: date = AS_OF) -> dict[str, Any]:
    return execute.run(tenant_id, compile_spec(spec, LAYER, tenant_id, as_of), "q1")


# --- The semantic layer -------------------------------------------------------------------


def test_the_semantic_layer_defines_the_first_version_metrics() -> None:
    assert LAYER.version == 1
    assert set(LAYER.metrics) == {
        "payment_volume_minor",
        "payment_success_rate",
        "lost_minor",
        "recoverable_minor",
        "recovered_minor",
        "fee_effective_rate",
        "fee_overcharge_minor",
        "unsettled_minor",
        "match_rate",
        "refund_amount_minor",
    }
    success = LAYER.catalogue()["metrics"]["payment_success_rate"]
    assert success["description"].startswith("Share of payment attempts that succeeded")
    assert {"provider", "method", "instrument_bank", "card_network", "upi_app", "hour"} <= set(
        success["dimensions"]
    )
    # A rate is reported in basis points; an amount keeps its own name.
    assert LAYER.key("payment_success_rate") == "payment_success_rate_bps"
    assert LAYER.key("lost_minor") == "lost_minor"


def test_a_period_is_a_word_that_code_turns_into_dates() -> None:
    assert resolve("last_seven_days", AS_OF) == (date(2026, 11, 10), AS_OF)
    assert resolve("last_month", AS_OF) == (date(2026, 10, 1), date(2026, 10, 31))
    assert resolve("this_month", AS_OF) == (date(2026, 11, 1), AS_OF)
    assert resolve("all_time", AS_OF) == (None, AS_OF)
    with pytest.raises(ValueError, match="period"):
        QuerySpec.model_validate({"metrics": ["lost_minor"], "period": "2026-10-01"})


# --- Answers against the records ----------------------------------------------------------


def test_a_metric_matches_the_same_question_asked_of_the_tables(merchant: Merchant) -> None:
    result = run(
        merchant.tenant_id,
        QuerySpec(
            metrics=["payment_volume_minor", "payment_success_rate"],
            dimensions=["provider"],
            period="last_seven_days",
        ),
    )
    with tenant_session(merchant.tenant_id) as session:
        truth = session.execute(
            text(
                """
                SELECT a.provider,
                       sum(a.amount_minor) FILTER (WHERE s.status = 'succeeded'),
                       count(*) FILTER (WHERE s.status = 'succeeded'),
                       count(*) FILTER (WHERE s.status IN ('succeeded', 'failed'))
                  FROM payment_attempt a
                  JOIN attempt_status s USING (tenant_id, provider, attempt_id)
                 WHERE (a.first_seen_at AT TIME ZONE 'Asia/Kolkata')::date
                       BETWEEN '2026-11-10' AND '2026-11-16'
                 GROUP BY a.provider
                """
            )
        ).all()

    expected = {
        provider: (int(volume), (succeeded * 10_000 + tried // 2) // tried)
        for provider, volume, succeeded, tried in truth
    }
    assert {
        row["provider"]: (row["payment_volume_minor"], row["payment_success_rate_bps"])
        for row in result["rows"]
    } == expected
    assert len(expected) == 3
    # Largest volume first, and the totals are for everything together.
    volumes = [row["payment_volume_minor"] for row in result["rows"]]
    assert volumes == sorted(volumes, reverse=True)
    assert result["totals"]["payment_volume_minor"] == sum(volumes)
    assert (result["period_start"], result["period_end"]) == ("2026-11-10", "2026-11-16")


def test_the_leak_metrics_are_the_engines_numbers(merchant: Merchant) -> None:
    result = run(
        merchant.tenant_id,
        QuerySpec(metrics=["lost_minor", "recoverable_minor"], dimensions=["family"]),
        TODAY,
    )

    assert result["rows"] == [
        {"family": "reconciliation", "lost_minor": 31_000_000, "recoverable_minor": 31_000_000},
        {"family": "fee_audit", "lost_minor": 26_000_000, "recoverable_minor": 26_000_000},
    ]
    assert run(merchant.tenant_id, QuerySpec(metrics=["fee_overcharge_minor"]), TODAY)[
        "totals"
    ] == {"fee_overcharge_minor": 26_000_000}
    assert run(merchant.tenant_id, QuerySpec(metrics=["recovered_minor"]), TODAY)["totals"] == {
        "recovered_minor": 0
    }
    assert run(merchant.tenant_id, QuerySpec(metrics=["match_rate"]))["totals"] == {
        "match_rate_bps": 10_000
    }


def test_filters_rows_and_order_shape_the_answer(merchant: Merchant) -> None:
    by_method = run(
        merchant.tenant_id,
        QuerySpec(
            metrics=["payment_volume_minor"],
            dimensions=["method"],
            filters=[Filter(dimension="provider", values=["razorpay"])],
            order="smallest_first",
            rows="first",
        ),
    )
    without = run(
        merchant.tenant_id,
        QuerySpec(
            metrics=["payment_volume_minor"],
            filters=[Filter(dimension="provider", values=["razorpay"], exclude=True)],
        ),
    )
    everything = run(merchant.tenant_id, QuerySpec(metrics=["payment_volume_minor"]))
    razorpay = run(
        merchant.tenant_id,
        QuerySpec(
            metrics=["payment_volume_minor"],
            filters=[Filter(dimension="provider", values=["razorpay"])],
        ),
    )

    assert [row["method"] for row in by_method["rows"]] == ["wallet"]
    assert by_method["totals"] == razorpay["totals"]
    assert (
        without["totals"]["payment_volume_minor"] + razorpay["totals"]["payment_volume_minor"]
        == everything["totals"]["payment_volume_minor"]
    )
    # A rate with nothing under it is no figure at all, not a zero.
    empty = run(
        merchant.tenant_id,
        QuerySpec(
            metrics=["payment_success_rate"],
            filters=[Filter(dimension="provider", values=["nobody"])],
        ),
    )
    assert empty["totals"] == {"payment_success_rate_bps": None}


# --- Asking, end to end -------------------------------------------------------------------


def test_where_did_we_lose_the_most_shows_the_reconciliation_and_fee_numbers(
    merchant: Merchant,
) -> None:
    seen = ask(merchant.tenant_id, "Where did we lose the most last week?", as_of=TODAY)

    assert seen["steps"] == ["understand", "compile", "run", "answer"]
    assert seen["query"]["spec"]["metrics"] == ["lost_minor"]
    assert seen["query"]["spec"]["period"] == "last_seven_days"
    [table] = seen["tables"]
    assert [column["label"] for column in table["columns"]] == ["Audit", "Money lost to leaks"]
    assert [row["display"] for row in table["rows"]] == [
        {"family": "reconciliation", "lost_minor": "₹3.1L"},
        {"family": "fee_audit", "lost_minor": "₹2.6L"},
    ]
    assert seen["chart"] == {
        "query_id": "q1",
        "type": "bar",
        "label_key": "family",
        "value_key": "lost_minor",
    }
    answer = seen["answer"]
    assert answer["text"] == (
        "Money lost to leaks by audit, in the last seven days: reconciliation is highest at "
        "₹3.1L. Overall it is ₹5.7L."
    )
    # The figures were put in by code: the model's text holds only references.
    assert grounding.bare_figures(answer["template"]) == []
    assert answer["values"]["q1.totals.lost_minor"] == 57_000_000
    assert answer["next_step"] == {"kind": "run_agent", "playbook": "leakage_audit"}
    assert seen["sources"]["query_ids"] == [f"{seen['done']['chat_id']}:q1"]


def test_a_why_question_uses_the_agents_tools(merchant: Merchant) -> None:
    seen = ask(merchant.tenant_id, "Why are we being overcharged on fees?", as_of=TODAY)

    assert seen["steps"] == ["understand", "analyse", "answer"]
    assert seen["answer"]["text"] == (
        "The engines have 3 open findings worth ₹2.6L. The largest is fee above contract at "
        "cashfree: ₹1.6L over 1,000 records in October 2026."
    )
    assert seen["answer"]["next_step"] == {"kind": "run_agent", "playbook": "fee_audit"}
    assert seen["tables"] == []
    assert seen["sources"]["query_ids"][0].endswith(":t1")


@pytest.mark.parametrize(
    ("question", "missing"),
    [
        ("What is our customer churn?", "customer churn"),
        ("What's the average order value by gateway?", "average order value"),
        ("How many payments failed yesterday?", "a count of records"),
        ("Will it rain in Mumbai tomorrow?", "what that question asks for"),
    ],
)
def test_a_question_that_needs_an_undefined_metric_is_refused_by_name(
    merchant: Merchant, question: str, missing: str
) -> None:
    seen = ask(merchant.tenant_id, question)

    assert seen["refusal"]["text"].startswith(
        f"I can't answer that: no metric is defined for {missing}."
    )
    assert "payment success rate" in seen["refusal"]["text"]
    assert seen["steps"] == ["understand"]
    assert "answer" not in seen and seen["tables"] == []


def test_a_split_the_metric_does_not_have_is_refused(merchant: Merchant) -> None:
    seen = ask(merchant.tenant_id, "Show refunds by bank")

    assert seen["refusal"]["text"] == (
        "I can't answer that: refund_amount_minor cannot be split or filtered by 'instrument_bank'."
    )


def test_every_question_is_in_the_audit_log_as_hashes(merchant: Merchant) -> None:
    seen = ask(merchant.tenant_id, "What is our payment volume by gateway?")
    with tenant_session(merchant.tenant_id) as session:
        rows = session.execute(
            select(m.AuditLog.kind, m.AuditLog.actor).where(
                m.AuditLog.ref.like(f"chat:{seen['done']['chat_id']}:%")
            )
        ).all()

    assert sorted(rows) == [("model_call", "chat"), ("model_call", "chat"), ("tool_call", "chat")]


# --- No path lets model text reach the database as SQL ------------------------------------


class Says:
    """A model that returns whatever plan and answer a test gives it."""

    name = "says"

    def __init__(self, plan: dict[str, Any], answer: dict[str, Any] | None = None) -> None:
        self.plan, self.reply = plan, answer or {"text": "Done."}

    def understand(self, question: str, layer: semantic.Layer) -> dict[str, Any]:
        return self.plan

    def answer(self, question: str, plan: Plan, results: dict[str, Any]) -> dict[str, Any]:
        return self.reply


def query(**spec: Any) -> dict[str, Any]:  # noqa: ANN401
    return {"kind": "answer", "steps": [{"kind": "query", "spec": spec}]}


@pytest.mark.parametrize(
    "plan",
    [
        {"kind": "answer", "sql": "SELECT * FROM payment_attempt"},
        {"kind": "answer", "steps": [{"kind": "sql", "text": "SELECT 1"}]},
        {"kind": "answer", "steps": [{"kind": "query", "sql": "SELECT 1"}]},
        query(metrics=["lost_minor"], sql="DROP TABLE finding"),
        query(metrics=["lost_minor"], where="1=1; DROP TABLE finding"),
        query(metrics=["lost_minor"], period="all_time'; DROP TABLE finding; --"),
        query(metrics=["lost_minor"], rows="1; DROP TABLE finding"),
        query(metrics=["lost_minor"], order="lost_minor; DROP TABLE finding"),
        "SELECT * FROM payment_attempt",
    ],
)
def test_a_plan_with_anywhere_to_put_sql_is_not_a_plan(merchant: Merchant, plan: Any) -> None:  # noqa: ANN401
    seen = ask(merchant.tenant_id, "anything", Says(plan))

    assert seen["refusal"]["text"] == service.NOT_UNDERSTOOD
    assert seen["steps"] == ["understand"]


@pytest.mark.parametrize(
    "plan",
    [
        query(metrics=["amount_minor) FROM payment_attempt; --"]),
        query(metrics=["lost_minor"], dimensions=["provider; DROP TABLE finding"]),
        query(metrics=["lost_minor"], dimensions=["tenant_id"]),
        query(metrics=["lost_minor"], filters=[{"dimension": "tenant_id", "values": ["x"]}]),
        query(metrics=["lost_minor"], filters=[{"dimension": "1=1 OR provider", "values": ["x"]}]),
        query(metrics=["lost_minor", "payment_volume_minor"]),
        {"kind": "answer", "steps": [{"kind": "tool", "name": "three_way_match"}]},
        {"kind": "answer", "steps": [{"kind": "tool", "name": "pg_read_file"}]},
    ],
)
def test_a_name_the_semantic_layer_does_not_define_is_refused_before_any_query(
    merchant: Merchant, plan: dict[str, Any]
) -> None:
    seen = ask(merchant.tenant_id, "anything", Says(plan))

    assert seen["refusal"]["text"].startswith("I can't answer that: ")
    assert "run" not in seen["steps"]
    assert seen["tables"] == []


def test_a_value_from_the_model_is_only_ever_a_bound_parameter(merchant: Merchant) -> None:
    spec = QuerySpec(
        metrics=["payment_volume_minor"],
        dimensions=["provider"],
        filters=[Filter(dimension="method", values=[INJECTION])],
    )
    compiled = compile_spec(spec, LAYER, merchant.tenant_id, AS_OF)
    statement = compiled.statement.compile(dialect=postgresql.dialect())  # type: ignore[no-untyped-call]
    before = merchant_rows(merchant.tenant_id)

    seen = ask(merchant.tenant_id, "anything", Says(query(**spec.model_dump())))

    assert "DROP" not in str(statement)
    assert INJECTION in statement.params.values() or [INJECTION] in statement.params.values()
    # It ran, matched no method, and changed nothing.
    assert seen["tables"][0]["rows"] == []
    assert merchant_rows(merchant.tenant_id) == before > 0


def merchant_rows(tenant_id: UUID) -> int:
    with tenant_session(tenant_id) as session:
        return session.scalar(select(func.count()).select_from(m.PaymentAttempt)) or 0


CHAT = Path(__file__).resolve().parents[1] / "revlane" / "chat"


def test_the_chat_code_builds_no_sql_from_text() -> None:
    """Statements come from the compiler's expression objects alone."""
    for path in sorted(CHAT.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        called = {
            node.func.id if isinstance(node.func, ast.Name) else node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name | ast.Attribute)
        }
        assert not called & {"text", "literal_column", "exec_driver_sql", "raw_connection"}, (
            path.name
        )
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("sqlalchemy")
            for alias in node.names
        }
        assert not imported & {"text", "literal_column", "TextClause"}, path.name
    # The one place a statement is turned into a string wraps a compiled statement.
    assert (CHAT / "execute.py").read_text().count("EXPLAIN (FORMAT JSON)") == 1
    # And the model only ever returns data that the plan's schema checks.
    assert 'extra="forbid"' in (CHAT / "spec.py").read_text()


def test_the_chat_role_can_read_the_views_and_nothing_else(merchant: Merchant) -> None:
    with chat_session(merchant.tenant_id) as session:
        visible = session.scalar(text("SELECT count(*) FROM chat_attempts"))
        assert visible == merchant_rows(merchant.tenant_id)
    for statement, problem in (
        ("SELECT count(*) FROM payment_attempt", "permission denied"),
        ("SELECT count(*) FROM tenant", "permission denied"),
        ("DELETE FROM chat_refunds", "read-only transaction|permission denied"),
        ("UPDATE chat_settlements SET net_minor = 0", "read-only transaction|permission denied"),
        ("CREATE TABLE x (y int)", "read-only transaction|permission denied"),
    ):
        with pytest.raises(DBAPIError, match=problem), chat_session(merchant.tenant_id) as session:
            session.execute(text(statement))


def test_a_query_that_runs_too_long_is_stopped(
    merchant: Merchant, monkeypatch: pytest.MonkeyPatch
) -> None:
    from revlane.db import session as db

    monkeypatch.setattr(db, "CHAT_TIMEOUT_MS", 50)
    with (
        pytest.raises(DBAPIError, match="statement timeout"),
        chat_session(merchant.tenant_id) as session,
    ):
        session.execute(text("SELECT pg_sleep(2)"))
    monkeypatch.setattr(execute, "MAX_COST", 1)
    seen = ask(merchant.tenant_id, "What is our payment volume by gateway?")
    assert seen["refusal"]["text"] == "That question would read too much at once. Narrow it down."


def test_an_answer_with_a_figure_of_its_own_is_never_shown(merchant: Merchant) -> None:
    plan = query(metrics=["lost_minor"])
    replies = (
        {"text": "You lost ₹9.9L, about 12% more than last week."},
        {"text": "Owed: {{q7.totals.lost_minor}}."},
        {"text": "Fine.", "sql": "SELECT 1"},
        {"text": ""},
    )

    for reply in replies:
        seen = ask(merchant.tenant_id, "anything", Says(plan, reply), as_of=TODAY)
        assert seen["answer"]["text"] == service.SEE_TABLE, reply
        assert seen["tables"][0]["totals"] == {"lost_minor": "₹5.7L"}


# --- Tenants ------------------------------------------------------------------------------


def test_a_cross_tenant_question_returns_nothing_from_another_tenant(
    merchant: Merchant, other: Merchant
) -> None:
    question = "What is our payment volume by gateway?"

    mine = ask(merchant.tenant_id, question)["tables"][0]
    theirs = ask(other.tenant_id, question)["tables"][0]

    def volumes(table: dict[str, Any]) -> dict[str, int]:
        return {
            row["values"]["provider"]: row["values"]["payment_volume_minor"]
            for row in table["rows"]
        }

    with tenant_session(merchant.tenant_id) as session:
        own: dict[str, int] = dict(
            session.execute(
                text(
                    "SELECT a.provider, sum(a.amount_minor)::bigint FROM payment_attempt a "
                    "JOIN attempt_status s USING (tenant_id, provider, attempt_id) "
                    "WHERE s.status = 'succeeded' GROUP BY 1"
                )
            ).all()
        )
    assert volumes(mine) == own
    assert volumes(theirs) != own and set(volumes(theirs)) == set(own)
    # Naming the other tenant in the plan gets nowhere: the tenant is not a dimension.
    aimed = Says(
        query(
            metrics=["payment_volume_minor"],
            filters=[{"dimension": "tenant_id", "values": [str(other.tenant_id)]}],
        )
    )
    assert "refusal" in ask(merchant.tenant_id, "anything", aimed)


def test_a_view_shows_one_tenant_even_to_a_query_that_asks_for_all(
    merchant: Merchant, other: Merchant
) -> None:
    views = (
        "chat_attempts",
        "chat_findings",
        "chat_settlement_lines",
        "chat_settlements",
        "chat_refunds",
    )
    for view in views:
        with chat_session(merchant.tenant_id) as session:
            tenants = set(session.scalars(text(f"SELECT DISTINCT tenant_id FROM {view}")))  # noqa: S608
        assert tenants == {merchant.tenant_id}, view
    with chat_session(uuid4()) as session:
        assert session.scalar(text("SELECT count(*) FROM chat_attempts")) == 0
    # The compiler pins the tenant too, so a mismatch returns nothing rather than someone's rows.
    crossed = compile_spec(
        QuerySpec(metrics=["payment_volume_minor"]), LAYER, other.tenant_id, AS_OF
    )
    assert execute.run(merchant.tenant_id, crossed, "q1")["totals"] == {"payment_volume_minor": 0}


def test_an_unknown_name_in_a_spec_is_an_error_not_a_query() -> None:
    for spec in (
        QuerySpec(metrics=["revenue"]),
        QuerySpec(metrics=["lost_minor"], dimensions=["hour"]),
        QuerySpec(metrics=["lost_minor", "lost_minor"]),
        QuerySpec(metrics=["lost_minor"], dimensions=["provider", "provider"]),
    ):
        with pytest.raises(SpecError):
            compile_spec(spec, LAYER, uuid4(), AS_OF)


# --- Over HTTP ----------------------------------------------------------------------------


def events(body: str) -> list[tuple[str, dict[str, Any]]]:
    found = []
    for block in body.strip().split("\n\n"):
        name, data = block.split("\n", 1)
        found.append((name.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    return found


def test_the_chat_endpoint_streams_steps_the_answer_a_table_and_the_query_ids() -> None:
    from revlane.bootstrap import ensure_tenant
    from revlane.config import get_settings

    settings = get_settings()
    ensure_tenant(settings.demo_tenant_id, settings.demo_tenant_name)
    app = create_app()
    sign_in(app, settings.demo_tenant_id)
    with TestClient(app) as client:
        answered = client.post(
            "/api/v1/chat", json={"question": "What is our success rate by gateway last week?"}
        )
        refused = client.post("/api/v1/chat", json={"question": "What is our churn?"})
        empty = client.post("/api/v1/chat", json={"question": ""})
        extra = client.post("/api/v1/chat", json={"question": "x", "sql": "SELECT 1"})

    assert answered.status_code == 200
    assert answered.headers["content-type"].startswith("text/event-stream")
    names = [name for name, _ in events(answered.text)]
    assert names[:5] == ["step", "step", "query", "step", "table"]
    assert names[-3:] == ["answer", "sources", "done"]
    by_name = dict(events(answered.text))
    assert by_name["query"]["spec"]["metrics"] == ["payment_success_rate"]
    assert by_name["query"]["spec"]["dimensions"] == ["provider"]
    assert by_name["sources"]["query_ids"][0].endswith(":q1")
    assert [name for name, _ in events(refused.text)] == ["step", "refusal", "done"]
    assert (empty.status_code, extra.status_code) == (422, 422)
