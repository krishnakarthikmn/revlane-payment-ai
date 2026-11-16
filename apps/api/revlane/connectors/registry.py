"""Every connector by provider, and how a file becomes raw records."""

import csv
import json
from collections.abc import Iterable, Iterator
from decimal import Decimal

from revlane.connectors.base import Connector, MappingError, Payload, RawRecord
from revlane.connectors.cashfree import CashfreeConnector
from revlane.connectors.files import BankConnector, MarketplaceConnector, OrdersConnector
from revlane.connectors.live import LiveAccess
from revlane.connectors.payu import PayUConnector
from revlane.connectors.razorpay import RazorpayConnector

PROVIDERS = ("razorpay", "payu", "cashfree", "marketplace", "bank", "orders")
# The two gateways with a sandbox connector (decision 1 in docs/decisions-needed.md).
LIVE_PROVIDERS = ("razorpay", "payu")


class UnknownFile(ValueError):
    """A file whose records this provider's connector does not recognise."""


def connector_for(provider: str, live: LiveAccess | None = None) -> Connector:
    if provider == "razorpay":
        return RazorpayConnector(live)
    if provider == "payu":
        return PayUConnector(live)
    if provider == "cashfree":
        return CashfreeConnector()
    if provider == "marketplace":
        return MarketplaceConnector()
    if provider == "bank":
        return BankConnector()
    if provider == "orders":
        return OrdersConnector()
    raise KeyError(f"No connector for {provider!r}")


def parse_json_line(line: str) -> Payload:
    """One JSON object. Decimals stay exact: nothing passes through a float."""
    try:
        value = json.loads(line, parse_float=Decimal)
    except json.JSONDecodeError as error:
        raise MappingError("Not valid JSON") from error
    if not isinstance(value, dict):
        raise MappingError("Not a JSON object")
    return value


def read_records(
    connector: Connector, filename: str, lines: Iterable[str]
) -> Iterator[RawRecord | MappingError]:
    """Raw records from a JSON-lines or CSV file, one at a time.

    A line that cannot be read comes back as the error, so one bad line does
    not stop the file. A file none of whose records the connector recognises
    raises `UnknownFile` on its first record.
    """
    if filename.lower().endswith(".csv"):
        payloads: Iterator[Payload | MappingError] = (
            dict(row) for row in csv.DictReader(lines) if any(row.values())
        )
    elif filename.lower().endswith((".jsonl", ".ndjson", ".json")):
        payloads = (_json(line) for line in lines if line.strip())
    else:
        raise UnknownFile(f"{filename}: only .csv and .jsonl files can be read.")

    recognised = False
    for payload in payloads:
        if isinstance(payload, MappingError):
            yield payload
            continue
        kind = connector.classify(payload)
        if kind is None:
            if not recognised:
                raise UnknownFile(
                    f"{filename} does not look like a {connector.provider} file Revlane can read."
                )
            yield MappingError("Unrecognised record")
            continue
        recognised = True
        yield RawRecord(connector.provider, kind, payload, "file")


def _json(line: str) -> Payload | MappingError:
    try:
        return parse_json_line(line)
    except MappingError as error:
        return error


def detect_provider(filename: str, lines: list[str]) -> str:
    """Which provider's file this is: the first whose connector both recognises
    and can map the first record. Two gateways may use the same record names,
    so recognising alone is not enough."""
    for provider in PROVIDERS:
        connector = connector_for(provider)
        try:
            first = next(read_records(connector, filename, iter(lines)), None)
            if first is None or isinstance(first, MappingError):
                continue
            connector.map(first)
        except UnknownFile as error:
            if "only .csv and .jsonl" in str(error):
                raise
            continue
        except MappingError:
            continue
        return provider
    raise UnknownFile(f"{filename} does not look like a file from any provider Revlane reads.")
