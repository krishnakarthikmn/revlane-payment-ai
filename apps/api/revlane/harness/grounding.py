"""Grounding by construction. Rule 2: the model never produces a number.

Model text refers to tool results, as in `{{t3.total_minor}}`. Code looks each
reference up in the results of that run and formats the value. Text that
carries a figure of its own is refused, and so is a reference that does not
lead to a value a tool returned.
"""

import re
from collections.abc import Mapping
from datetime import date
from typing import Any

from revlane.money import format_inr, group_indian

REFERENCE = re.compile(r"\{\{\s*([^{}]*?)\s*\}\}")
# A tool result (t1) or, in chat, a query result (q1), then a path into it.
PATH = re.compile(r"^[tq][1-9][0-9]*(\.[A-Za-z_][A-Za-z0-9_]*|\.[0-9]+)*$")
# Digits of any script, and the words that turn into an amount without one.
DIGIT = re.compile(r"\d")
AMOUNT_WORDS = re.compile(
    r"(?<![A-Za-z])(hundred|thousand|lakh|lakhs|lac|lacs|crore|crores|million|billion|"
    r"percent|per cent|rupee|rupees|paise|rs|inr)(?![A-Za-z])",
    re.IGNORECASE,
)
SYMBOLS = ("₹", "%", "$")
# Free text from outside (a bank narration, a file's contents) is wrapped
# under this key by the tools and can never be pulled into output.
UNTRUSTED = "untrusted_text"
MAX_TOKEN = 64
MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
MONTH = re.compile(r"^(\d{4})-(\d{2})$")


class GroundingError(ValueError):
    pass


def references(text: str) -> list[str]:
    return [match.group(1) for match in REFERENCE.finditer(text)]


def bare_figures(text: str) -> list[str]:
    """Every figure the text carries on its own, outside a reference."""
    plain = REFERENCE.sub(" ", text)
    found = [match.group(0) for match in re.finditer(r"\d[\d,.]*", plain)]
    found += [match.group(0) for match in AMOUNT_WORDS.finditer(plain)]
    found += [symbol for symbol in SYMBOLS if symbol in plain]
    if "{{" in plain or "}}" in plain:
        found.append("{{")
    return found


def resolve(path: str, results: Mapping[str, Any]) -> object:
    """The value a reference leads to in this run's tool results."""
    if not PATH.match(path):
        raise GroundingError(f"{path!r} is not a reference to a tool result.")
    head, *rest = path.split(".")
    if head not in results:
        raise GroundingError(f"{head} is not a tool result of this run.")
    value: object = results[head]
    for part in rest:
        if part == UNTRUSTED:
            raise GroundingError(f"{path} points at text from outside, which is never quoted.")
        if isinstance(value, Mapping) and part in value:
            value = value[part]
        elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        else:
            raise GroundingError(f"{path} leads to nothing in {head}.")
    return value


def format_value(path: str, value: object) -> str:
    """A resolved value as a person reads it. The last part of the path says what it is."""
    name = path.rsplit(".", 1)[-1]
    if isinstance(value, bool) or value is None:
        raise GroundingError(f"{path} is not a figure or a name.")
    if isinstance(value, int):
        if name.endswith("_minor"):
            return format_inr(value)
        if name.endswith("_bps"):
            return f"{value // 100}.{value % 100:02d}%"
        return group_indian(value)
    if isinstance(value, str):
        month = MONTH.match(value)
        if month and 1 <= int(month.group(2)) <= 12:
            return f"{MONTHS[int(month.group(2)) - 1]} {month.group(1)}"
        try:
            day = date.fromisoformat(value)
        except ValueError:
            pass
        else:
            return f"{day.day} {MONTHS[day.month - 1]} {day.year}"
        # A name or an id: one short token. Anything longer could be someone's sentence.
        if 0 < len(value) <= MAX_TOKEN and not any(ch.isspace() for ch in value):
            return value
    raise GroundingError(f"{path} is not a figure or a name.")


def check_text(text: str, results: Mapping[str, Any]) -> list[str]:
    """What is wrong with a piece of model text. Empty when it is grounded."""
    problems = [f"bare figure {figure!r}" for figure in bare_figures(text)]
    for path in references(text):
        try:
            format_value(path, resolve(path, results))
        except GroundingError as error:
            problems.append(str(error))
    return problems


def render(text: str, results: Mapping[str, Any]) -> str:
    """The text with every reference replaced by its formatted value."""
    return REFERENCE.sub(
        lambda match: format_value(match.group(1), resolve(match.group(1), results)), text
    )


def values(texts: list[str], results: Mapping[str, Any]) -> dict[str, Any]:
    """Each reference used in the texts, with the raw value it resolves to."""
    return {path: resolve(path, results) for text in texts for path in references(text)}
