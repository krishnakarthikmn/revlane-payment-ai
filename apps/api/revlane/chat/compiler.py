"""Compiles a query spec into a statement over the read-only chat views.

Every table and column name comes from the semantic layer, looked up by
key: a name the layer does not define is refused before anything is built.
Every value from the spec becomes a bound parameter. No string of SQL is
assembled here, so nothing a model writes can become SQL.
"""

from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    ColumnElement,
    Select,
    and_,
    case,
    cast,
    column,
    func,
    or_,
    select,
    table,
    true,
)
from sqlalchemy.sql.expression import FromClause

from revlane.chat.semantic import Conditions, Layer, Metric, Part
from revlane.chat.spec import MAX_ROWS, ROW_LIMITS, QuerySpec, resolve

BPS = 10_000


class SpecError(ValueError):
    """The spec asks for something the semantic layer does not define."""


@dataclass(frozen=True)
class Compiled:
    statement: Select[Any]
    # The same question with no split: the figure for everything together.
    totals: Select[Any]
    dimensions: tuple[str, ...]
    keys: tuple[str, ...]
    period_start: date | None
    period_end: date


def _conditions(view: FromClause, where: Conditions) -> ColumnElement[bool]:
    return and_(true(), *(view.c[name].in_(values) for name, values in where.items()))


def _part(view: FromClause, part: Part) -> ColumnElement[Any]:
    measured = func.count() if part.count else func.sum(view.c[part.sum or ""])
    return func.coalesce(cast(measured.filter(_conditions(view, part.where)), BigInteger), 0)


def _value(view: FromClause, metric: Metric) -> ColumnElement[Any]:
    if metric.numerator is None or metric.denominator is None:
        return _part(view, Part(sum=metric.sum, where=metric.where))
    top, bottom = _part(view, metric.numerator), _part(view, metric.denominator)
    # A rate in basis points, rounded half up, in whole numbers throughout.
    return case((bottom == 0, None), else_=cast((top * BPS + bottom // 2) // bottom, BigInteger))


def _has_rows(view: FromClause, metric: Metric) -> ColumnElement[bool]:
    """Does the group hold any row the metric is measured over?"""
    where = metric.denominator.where if metric.denominator is not None else metric.where
    return func.count().filter(_conditions(view, where)) > 0


def _view(layer: Layer, source_name: str) -> FromClause:
    source = layer.sources[source_name]
    names = {"tenant_id", source.time, *source.dimensions.values()}
    for metric in layer.metrics.values():
        if metric.source != source_name:
            continue
        parts = [Part(sum=metric.sum, where=metric.where)] if metric.sum else []
        parts += [p for p in (metric.numerator, metric.denominator) if p is not None]
        for part in parts:
            names |= set(part.where) | ({part.sum} if part.sum else set())
    return table(source.view, *(column(name) for name in sorted(names)))


def compile_spec(spec: QuerySpec, layer: Layer, tenant_id: UUID, as_of: date) -> Compiled:
    unknown = [name for name in spec.metrics if name not in layer.metrics]
    if unknown:
        raise SpecError(f"No metric is defined for {unknown[0]!r}.")
    if len(set(spec.metrics)) != len(spec.metrics):
        raise SpecError("The same metric is asked for twice.")
    sources = {layer.metrics[name].source for name in spec.metrics}
    if len(sources) > 1:
        raise SpecError(f"{' and '.join(spec.metrics)} cannot be answered together.")
    source_name = sources.pop()
    source = layer.sources[source_name]
    asked = [*spec.dimensions, *(f.dimension for f in spec.filters)]
    for name in asked:
        if name not in source.dimensions:
            raise SpecError(f"{spec.metrics[0]} cannot be split or filtered by {name!r}.")
    if len(set(spec.dimensions)) != len(spec.dimensions):
        raise SpecError("The same dimension is asked for twice.")

    view = _view(layer, source_name)
    start, end = resolve(spec.period, as_of)
    # The tenant is pinned here as well as in the view and the session.
    conditions: list[ColumnElement[bool]] = [
        view.c.tenant_id == tenant_id,
        view.c[source.time] <= end,
    ]
    if start is not None:
        conditions.append(view.c[source.time] >= start)
    for wanted in spec.filters:
        chosen = view.c[source.dimensions[wanted.dimension]].in_(wanted.values)
        conditions.append(~chosen if wanted.exclude else chosen)

    keys = tuple(layer.key(name) for name in spec.metrics)
    values = [
        _value(view, layer.metrics[name]).label(key)
        for name, key in zip(spec.metrics, keys, strict=True)
    ]
    splits = [view.c[source.dimensions[name]].label(name) for name in spec.dimensions]
    totals = select(*values).where(*conditions)
    statement = select(*splits, *values).where(*conditions)
    if splits:
        # A group none of the metrics has a row for is not part of the answer.
        counted = or_(*(_has_rows(view, layer.metrics[name]) for name in spec.metrics))
        statement = statement.group_by(*splits).having(counted)
        first = values[0]  # rows are ranked by the first metric asked for
        if spec.order == "largest_first":
            statement = statement.order_by(first.desc().nulls_last(), *splits)
        elif spec.order == "smallest_first":
            statement = statement.order_by(first.asc().nulls_last(), *splits)
        else:
            statement = statement.order_by(*splits)
    statement = statement.limit(min(ROW_LIMITS[spec.rows], MAX_ROWS))
    return Compiled(statement, totals, tuple(spec.dimensions), keys, start, end)
