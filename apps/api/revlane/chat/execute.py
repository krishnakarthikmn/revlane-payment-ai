"""Runs a compiled query in the read-only chat session, inside its limits."""

from typing import Any
from uuid import UUID

from sqlalchemy import Select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.compiler import SQLCompiler
from sqlalchemy.sql.expression import ClauseElement, Executable

from revlane.chat.compiler import Compiled
from revlane.db.session import chat_session

# The planner's estimate above which a query is refused without being run.
MAX_COST = 2_000_000


class TooExpensive(RuntimeError):
    pass


class Explain(Executable, ClauseElement):
    """`EXPLAIN` for a statement our own compiler built. Parameters stay bound."""

    inherit_cache = False

    def __init__(self, statement: Select[Any]) -> None:
        self.statement = statement


@compiles(Explain, "postgresql")
def _explain(element: Explain, compiler: SQLCompiler, **kwargs: Any) -> str:  # noqa: ANN401
    return "EXPLAIN (FORMAT JSON) " + compiler.process(element.statement, **kwargs)


def _plain(value: object) -> object:
    """Whole numbers stay whole numbers; anything else is text."""
    if value is None or isinstance(value, bool | int | str):
        return value
    return str(value)


def run(tenant_id: UUID, compiled: Compiled, query_id: str) -> dict[str, Any]:
    """The result of one query, in the shape answers refer to."""
    try:
        with chat_session(tenant_id) as session:
            plan = session.execute(Explain(compiled.statement)).scalar_one()
            if plan[0]["Plan"]["Total Cost"] > MAX_COST:
                raise TooExpensive("That question would read too much at once. Narrow it down.")
            names = [*compiled.dimensions, *compiled.keys]
            rows = [
                {name: _plain(value) for name, value in zip(names, row, strict=True)}
                for row in session.execute(compiled.statement)
            ]
            totals = dict(
                zip(
                    compiled.keys,
                    (_plain(v) for v in session.execute(compiled.totals).one()),
                    strict=True,
                )
            )
    except DBAPIError as error:
        if "statement timeout" in str(error.orig):
            raise TooExpensive("That question took too long to answer. Narrow it down.") from error
        raise
    return {
        "query_id": query_id,
        "dimensions": list(compiled.dimensions),
        "metrics": list(compiled.keys),
        "period_start": compiled.period_start.isoformat() if compiled.period_start else None,
        "period_end": compiled.period_end.isoformat(),
        "row_count": len(rows),
        "rows": rows,
        "top": rows[0] if rows else None,
        "totals": totals,
    }
