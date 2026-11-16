"""The query spec: what a question asks for, in the semantic layer's own words.

Rule 3: the model never writes SQL. It fills in this structure and nothing
else; code compiles it. The spec holds no dates and no figures: a period is
one of a fixed set of words, and so is the number of rows wanted.
"""

from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Period = Literal[
    "last_seven_days",
    "last_thirty_days",
    "last_ninety_days",
    "this_month",
    "last_month",
    "all_time",
]
Rows = Literal["first", "top_five", "top_ten", "all"]
ROW_LIMITS = {"first": 1, "top_five": 5, "top_ten": 10, "all": 200}
# No answer is ever longer than this, whatever is asked for.
MAX_ROWS = 200


class Filter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension: str
    # One of these values; `exclude` turns it into none of them.
    values: list[str] = Field(min_length=1, max_length=20)
    exclude: bool = False


class QuerySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metrics: list[str] = Field(min_length=1, max_length=4)
    dimensions: list[str] = Field(default_factory=list, max_length=2)
    filters: list[Filter] = Field(default_factory=list, max_length=6)
    period: Period = "all_time"
    order: Literal["largest_first", "smallest_first", "by_dimension"] = "largest_first"
    rows: Rows = "all"


def resolve(period: str, as_of: date) -> tuple[date | None, date]:
    """The dates a period means on a given day. Both ends are included."""
    first_of_month = as_of.replace(day=1)
    if period == "last_seven_days":
        return as_of - timedelta(days=6), as_of
    if period == "last_thirty_days":
        return as_of - timedelta(days=29), as_of
    if period == "last_ninety_days":
        return as_of - timedelta(days=89), as_of
    if period == "this_month":
        return first_of_month, as_of
    if period == "last_month":
        last = first_of_month - timedelta(days=1)
        return last.replace(day=1), last
    return None, as_of
