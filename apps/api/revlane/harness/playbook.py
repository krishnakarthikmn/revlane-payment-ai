"""Playbooks: what an agent may read, what it may propose, and when to escalate.

A playbook is a YAML file. Loading one checks it against what the harness
actually has: every tool must exist, every action type must be one the harness
can carry out, and every action must require approval.
"""

import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from revlane.db.models import FINDING_TYPES

FOLDER = Path(__file__).parent / "playbooks"
# What the first version can carry out. Each one only drafts a document.
ACTION_TYPES = (
    "draft_fee_claim",
    "draft_settlement_query",
    "draft_reoffer_list",
    "draft_leakage_report",
)
OUTPUT_SCHEMAS = ("FindingAndProposal_v1",)
CONFIDENCE_BELOW = re.compile(r"^confidence\s*<\s*(0?\.\d+|1(\.0+)?)$")


class PlaybookError(ValueError):
    pass


class Limits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # A finding smaller than this is not worth a claim.
    min_claim_minor: int = Field(default=0, ge=0)
    max_claims_per_run: int = Field(default=20, ge=1, le=100)
    max_findings_per_proposal: int = Field(default=20, ge=1, le=200)


class ActionRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    # The first version has no other setting: nothing happens without a person.
    approval: Literal["required"]
    # Finding types this action is for. Empty means any.
    applies_to: list[str] = Field(default_factory=list)
    limits: Limits = Field(default_factory=Limits)

    @model_validator(mode="after")
    def known(self) -> "ActionRule":
        if self.type not in ACTION_TYPES:
            raise ValueError(f"no action called {self.type}")
        unknown = sorted(set(self.applies_to) - set(FINDING_TYPES))
        if unknown:
            raise ValueError(f"no finding type called {unknown[0]}")
        return self


class Playbook(BaseModel):
    model_config = ConfigDict(extra="forbid")

    playbook: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    version: int = Field(ge=1)
    trigger: list[dict[str, str]] = Field(default_factory=list)
    read_tools: list[str]
    actions: list[ActionRule]
    output_schema: str
    escalate_if: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def consistent(self) -> "Playbook":
        from revlane.harness.tools import REGISTRY

        unknown = sorted(set(self.read_tools) - set(REGISTRY))
        if unknown:
            raise ValueError(f"no tool called {unknown[0]}")
        if self.output_schema not in OUTPUT_SCHEMAS:
            raise ValueError(f"no output schema called {self.output_schema}")
        for condition in self.escalate_if:
            if condition != "contract_missing" and not CONFIDENCE_BELOW.match(condition):
                raise ValueError(f"cannot read the escalation rule {condition!r}")
        return self

    @property
    def name(self) -> str:
        return self.playbook

    @property
    def tools(self) -> frozenset[str]:
        return frozenset(self.read_tools)

    def action(self, action_type: str) -> ActionRule | None:
        return next((rule for rule in self.actions if rule.type == action_type), None)

    @property
    def escalates_on_missing_contract(self) -> bool:
        return "contract_missing" in self.escalate_if

    @property
    def confidence_floor_bps(self) -> int | None:
        """The confidence below which a run is escalated, in basis points of one."""
        for condition in self.escalate_if:
            match = CONFIDENCE_BELOW.match(condition)
            if match:
                digits = (match.group(1).split(".") + [""])[1].ljust(4, "0")[:4]
                return 10_000 if match.group(1).startswith("1") else int(digits)
        return None


def parse(text: str, source: str = "playbook") -> Playbook:
    try:
        raw: Any = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise PlaybookError(f"{source} is not valid YAML.") from error
    if not isinstance(raw, dict):
        raise PlaybookError(f"{source} is not a playbook.")
    try:
        return Playbook.model_validate(raw)
    except ValidationError as error:
        first = error.errors()[0]
        where = ".".join(str(part) for part in first["loc"])
        raise PlaybookError(f"{source}: {where}: {first['msg']}".replace(": : ", ": ")) from error


@lru_cache
def load_all() -> dict[str, Playbook]:
    found = {}
    for path in sorted(FOLDER.glob("*.yaml")):
        playbook = parse(path.read_text(encoding="utf-8"), path.name)
        if playbook.name != path.stem:
            raise PlaybookError(f"{path.name} holds the playbook {playbook.name}.")
        found[playbook.name] = playbook
    return found


def get(name: str) -> Playbook:
    playbooks = load_all()
    if name not in playbooks:
        raise PlaybookError(f"No playbook called {name}.")
    return playbooks[name]
