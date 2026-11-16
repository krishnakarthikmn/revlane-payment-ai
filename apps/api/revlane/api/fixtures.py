"""Sample data for the console's preview surfaces, taken from the prototype."""

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import APIRouter

router = APIRouter()

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "acme_travel.json"


@lru_cache
def load_fixtures() -> dict[str, Any]:
    with FIXTURE_PATH.open(encoding="utf-8") as handle:
        data: dict[str, Any] = json.load(handle)
    return data


@router.get("/fixtures")
def fixtures() -> dict[str, Any]:
    return load_fixtures()
