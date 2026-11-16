"""The finding lifecycle and how a new engine run meets the findings already stored."""

from datetime import date

import pytest

from revlane.engine import findings as fd


def NOV(day: int) -> date:
    return date(2026, 11, day)


NOV_10 = date(2026, 11, 10)


def item(item_id: str, amount: int = 100, on: date = NOV_10) -> fd.Item:
    return fd.Item(item_id, amount, on, (("attempts", item_id),))


def detected(*items: fd.Item) -> fd.Detected:
    [found] = fd.group("captured_not_settled", "payu", "no_settlement_line", list(items), "1")
    return found


def existing(
    state: str, *item_ids: str, sequence: int = 1, amount: int | None = None, covers: bool = True
) -> fd.Existing:
    return fd.Existing(
        id=f"f{sequence}",
        key="captured_not_settled:payu:no_settlement_line:2026-11",
        sequence=sequence,
        state=state,
        amount_minor=100 * len(item_ids) if amount is None else amount,
        item_ids=frozenset(item_ids),
        covers=covers,
    )


# --- States -------------------------------------------------------------------------


def test_every_finding_type_starts_as_recoverable() -> None:
    assert set(fd.INITIAL_STATE) == {
        "paid_auto_refunded",
        "settled_short",
        "captured_not_settled",
        "fee_above_contract",
        "tax_on_refunded_fee",
        "fee_not_in_contract",
    }
    assert set(fd.INITIAL_STATE.values()) == {"recoverable"}


@pytest.mark.parametrize(
    ("from_state", "to_state"),
    [
        ("recoverable", "claimed"),
        ("recoverable", "lost"),
        ("recoverable", "written_off"),
        ("lost", "recoverable"),
        ("lost", "written_off"),
        ("claimed", "recovered"),
        ("claimed", "recoverable"),
        ("claimed", "written_off"),
        ("written_off", "recoverable"),
    ],
)
def test_allowed_transitions(from_state: str, to_state: str) -> None:
    fd.check_transition(from_state, to_state)


@pytest.mark.parametrize(
    ("from_state", "to_state"),
    [
        ("recoverable", "recovered"),  # money is recovered only through a claim
        ("lost", "claimed"),
        ("lost", "recovered"),
        ("recovered", "recoverable"),
        ("recovered", "written_off"),
        ("written_off", "claimed"),
        ("claimed", "claimed"),
        ("claimed", "paid"),
    ],
)
def test_refused_transitions(from_state: str, to_state: str) -> None:
    with pytest.raises(fd.InvalidTransition):
        fd.check_transition(from_state, to_state)


# --- Grouping --------------------------------------------------------------------------


def test_items_are_grouped_by_month_in_a_stable_order() -> None:
    groups = fd.group(
        "settled_short",
        "marketplace",
        "payout_below_lines",
        [item("b", 5, NOV(8)), item("a", 7, NOV(8)), item("z", 1, date(2026, 10, 31))],
        "1",
    )

    assert [(g.period_start, g.period_end, g.amount_minor) for g in groups] == [
        (date(2026, 10, 1), date(2026, 10, 31), 1),
        (NOV(1), NOV(30), 12),
    ]
    assert [i.id for i in groups[1].items] == ["a", "b"]
    assert groups[1].key == "settled_short:marketplace:payout_below_lines:2026-11"
    assert fd.group("settled_short", "marketplace", "x", [], "1") == []


# --- A new run meets what is stored --------------------------------------------------------


def test_a_new_finding_is_created() -> None:
    found = detected(item("a"), item("b"))

    assert fd.reconcile([], [found]) == [fd.Create(found, 1)]


def test_a_run_that_finds_the_same_thing_changes_nothing() -> None:
    assert fd.reconcile([existing("recoverable", "a", "b")], [detected(item("a"), item("b"))]) == []


def test_an_open_finding_follows_what_the_engine_now_finds() -> None:
    found = detected(item("a"), item("c"))

    assert fd.reconcile([existing("recoverable", "a", "b")], [found]) == [fd.Update("f1", found)]
    # The same records with a different amount is also a change.
    assert fd.reconcile([existing("lost", "a", "c", amount=5)], [found]) == [fd.Update("f1", found)]


def test_an_open_finding_the_engine_no_longer_finds_is_written_off() -> None:
    [action] = fd.reconcile([existing("recoverable", "a")], [])

    assert action == fd.Move("f1", "recoverable", "written_off", fd.NO_LONGER_FOUND)


def test_a_claimed_finding_the_engine_no_longer_finds_has_been_recovered() -> None:
    [action] = fd.reconcile([existing("claimed", "a", "b")], [])

    assert action == fd.Move("f1", "claimed", "recovered", fd.MONEY_ARRIVED)
    # Part of it still outstanding: it stays claimed.
    assert fd.reconcile([existing("claimed", "a", "b")], [detected(item("a"))]) == []


def test_records_already_claimed_are_not_reported_again() -> None:
    claimed = existing("claimed", "a", "b")

    # Nothing new: no second finding.
    assert fd.reconcile([claimed], [detected(item("a"), item("b"))]) == []
    # One new record: a follow-on finding holding only that record.
    [action] = fd.reconcile([claimed], [detected(item("a"), item("b"), item("c", 250))])
    assert isinstance(action, fd.Create)
    assert action.sequence == 2
    assert [(i.id, i.amount_minor) for i in action.detected.items] == [("c", 250)]
    assert action.detected.amount_minor == 250


def test_records_a_person_wrote_off_stay_written_off() -> None:
    assert fd.reconcile([existing("written_off", "a")], [detected(item("a"))]) == []
    assert fd.reconcile([existing("recovered", "a")], [detected(item("a"))]) == []


def test_a_finding_the_engine_closed_itself_comes_back_if_the_leak_does() -> None:
    closed_by_engine = existing("written_off", "a", covers=False)
    found = detected(item("a"))

    assert fd.reconcile([closed_by_engine], [found]) == [fd.Create(found, 2)]


def test_an_open_finding_shrinks_to_what_is_not_already_claimed() -> None:
    stored = [existing("claimed", "a"), existing("recoverable", "a", "b", sequence=2)]

    [action] = fd.reconcile(stored, [detected(item("a"), item("b"))])

    assert isinstance(action, fd.Update)
    assert action.finding_id == "f2"
    assert [i.id for i in action.detected.items] == ["b"]
