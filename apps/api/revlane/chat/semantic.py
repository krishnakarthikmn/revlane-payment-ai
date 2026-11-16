"""Loads the semantic layer: the only names a query spec may use."""

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

FOLDER = Path(__file__).parent / "semantic"
CURRENT = "v1"
NAME = r"^[a-z][a-z0-9_]*$"
Conditions = dict[str, list[str | bool]]


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid")

    view: str = Field(pattern=NAME)
    # The date column a time range applies to.
    time: str = Field(pattern=NAME)
    # What a person may split or filter by, and the view column behind each.
    dimensions: dict[str, str]


class Part(BaseModel):
    """One side of a ratio: a count of rows or the sum of a column."""

    model_config = ConfigDict(extra="forbid")

    count: bool = False
    sum: str | None = Field(default=None, pattern=NAME)
    where: Conditions = Field(default_factory=dict)

    @model_validator(mode="after")
    def one_of(self) -> "Part":
        if self.count == (self.sum is not None):
            raise ValueError("a part is either a count or a sum")
        return self


class Metric(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str
    source: str
    sum: str | None = Field(default=None, pattern=NAME)
    where: Conditions = Field(default_factory=dict)
    numerator: Part | None = None
    denominator: Part | None = None

    @model_validator(mode="after")
    def sum_or_ratio(self) -> "Metric":
        ratio = self.numerator is not None and self.denominator is not None
        if ratio == (self.sum is not None) or (self.numerator is None) != (
            self.denominator is None
        ):
            raise ValueError("a metric is either a sum or a ratio of two parts")
        return self

    @property
    def is_ratio(self) -> bool:
        return self.numerator is not None


class Layer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int
    sources: dict[str, Source]
    metrics: dict[str, Metric]

    @model_validator(mode="after")
    def consistent(self) -> "Layer":
        for name, metric in self.metrics.items():
            if metric.source not in self.sources:
                raise ValueError(f"{name} reads a source that is not defined")
        return self

    def key(self, metric: str) -> str:
        """The name a metric's value has in a result. The ending tells the
        formatter what it is: paise, or a rate in basis points."""
        return f"{metric}_bps" if self.metrics[metric].is_ratio else metric

    def catalogue(self) -> dict[str, Any]:
        """What a model is told it may ask for."""
        return {
            "metrics": {
                name: {
                    "description": metric.description,
                    "dimensions": sorted(self.sources[metric.source].dimensions),
                }
                for name, metric in self.metrics.items()
            }
        }


@lru_cache
def load(version: str = CURRENT) -> Layer:
    raw = yaml.safe_load((FOLDER / f"{version}.yaml").read_text(encoding="utf-8"))
    return Layer.model_validate(raw)
