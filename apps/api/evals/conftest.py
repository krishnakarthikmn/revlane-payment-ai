"""`make eval`: the evaluation layers from docs/technical-design.md, section 9.

Runs in stub mode against the synthetic merchant in the test database. Each
layer records a score and its pass bar; the table is printed at the end.
"""

from dataclasses import dataclass

import pytest

from tests.conftest import database  # noqa: F401  (the migrated test database)


@dataclass
class Score:
    layer: str
    measure: str
    result: str
    bar: str
    # None for a layer that is not built yet.
    passed: bool | None


SCORES: list[Score] = []


def record(layer: str, measure: str, result: str, bar: str, passed: bool | None) -> None:
    SCORES.append(Score(layer, measure, result, bar, passed))


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    if not SCORES:
        return
    write = terminalreporter.write_line
    terminalreporter.section("Evaluation (stub mode)")
    write(f"{'Layer':<19}{'Measure':<58}{'Result':<12}{'Bar':<12}")
    for score in SCORES:
        mark = {True: "pass", False: "FAIL", None: "not run"}[score.passed]
        write(f"{score.layer:<19}{score.measure:<58}{score.result:<12}{score.bar:<12}{mark}")
