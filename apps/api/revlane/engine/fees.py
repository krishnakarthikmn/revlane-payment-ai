"""The fee check. See docs/technical-design.md, section 6.

For each settlement line, the expected fee is recomputed from the contract in
force on the transaction date: rate times gross, plus any fixed fee, plus tax
on the fee. Three things are reported:

- `fee_above_contract`: fee and tax charged above fee and tax agreed;
- `tax_on_refunded_fee`: tax kept on a fee that was handed back, where the
  contract says the tax comes back with it;
- `fee_not_in_contract`: a fee type the contract does not allow.

Pure functions. Change a rule, raise `RULES_VERSION`.
"""

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date

from revlane.engine.findings import Detected, Item, group
from revlane.engine.records import Attempt, Contract, Line, Refund, Settlement
from revlane.money import bps_of

RULES_VERSION = "1"

ABOVE_CONTRACT = "fee_above_contract"
TAX_ON_REFUNDED_FEE = "tax_on_refunded_fee"
NOT_IN_CONTRACT = "fee_not_in_contract"

CONTRACT_MISSING = "contract_missing"
PAYMENT_MISSING = "payment_missing"
SETTLEMENT_MISSING = "settlement_missing"


@dataclass(frozen=True, slots=True)
class FeeRules:
    # A line may be off by ₹1 or by 0.05% of its gross, whichever is larger.
    tolerance_minor: int = 100
    tolerance_bps: int = 5

    @classmethod
    def from_policy(cls, policy: Mapping[str, object]) -> "FeeRules":
        """A tenant's own values, from `tenant.policy["fees"]`."""
        default = cls()

        def whole(name: str, fallback: int) -> int:
            value = policy.get(name)
            return int(value) if isinstance(value, int | str) else fallback

        return cls(
            tolerance_minor=whole("tolerance_minor", default.tolerance_minor),
            tolerance_bps=whole("tolerance_bps", default.tolerance_bps),
        )

    def tolerance(self, gross_minor: int) -> int:
        return max(self.tolerance_minor, bps_of(abs(gross_minor), self.tolerance_bps))


@dataclass(frozen=True, slots=True)
class FeeDifference:
    type: str
    provider: str
    cause: str
    line_id: str
    attempt_id: str | None
    refund_id: str | None
    amount_minor: int
    # Fee and tax together: what the line carries, and what the contract gives.
    charged_minor: int
    expected_minor: int
    on: date  # the settlement date: a claim is made against a settlement

    def item(self) -> Item:
        records = [("settlement_lines", self.line_id)]
        if self.attempt_id:
            records.append(("attempts", self.attempt_id))
        if self.refund_id:
            records.append(("refunds", self.refund_id))
        return Item(self.line_id, self.amount_minor, self.on, tuple(records))


@dataclass
class FeeResult:
    differences: list[FeeDifference] = field(default_factory=list)
    findings: list[Detected] = field(default_factory=list)
    checked: int = 0
    # Lines that could not be checked, by reason.
    unchecked: dict[str, int] = field(default_factory=dict)


def expected_fee(contract: Contract, gross_minor: int) -> tuple[int, int]:
    """The fee and the tax on it that the contract gives for one payment."""
    fee = bps_of(gross_minor, contract.rate_bps) + contract.fixed_fee_minor
    return fee, bps_of(fee, contract.tax_rate_bps)


def contract_for(
    contracts: Sequence[Contract],
    provider: str,
    method: str | None,
    card_network: str | None,
    card_type: str | None,
    on: date,
) -> Contract | None:
    """The contract row in force on a date. A row naming the card network or
    type beats one that covers them all; among equals the newest wins."""
    candidates = [
        contract
        for contract in contracts
        if contract.provider == provider
        and contract.method == method
        and contract.card_network in (None, card_network)
        and contract.card_type in (None, card_type)
        and contract.in_force(on)
    ]
    return max(
        candidates,
        key=lambda c: (c.card_network is not None, c.card_type is not None, c.effective_from),
        default=None,
    )


def _cause_above(contract: Contract) -> str:
    cause = f"rate_above_contract:{contract.method}:{contract.card_network or 'any'}"
    return f"{cause}:{contract.card_type}" if contract.card_type else cause


def _difference(
    line: Line,
    on: date,
    kind: str,
    cause: str,
    amount: int,
    charged: int,
    expected: int,
    refund_id: str | None = None,
) -> FeeDifference:
    return FeeDifference(
        kind,
        line.provider,
        cause,
        line.line_id,
        line.attempt_id,
        refund_id,
        amount,
        charged,
        expected,
        on,
    )


def check_fees(
    *,
    lines: Sequence[Line],
    settlements: Sequence[Settlement],
    attempts: Sequence[Attempt],
    refunds: Sequence[Refund],
    contracts: Sequence[Contract],
    rules: FeeRules | None = None,
) -> FeeResult:
    rules = rules or FeeRules()
    settled_on = {settlement.key: settlement.settled_on for settlement in settlements}
    attempt_of = {attempt.key: attempt for attempt in attempts}
    refund_ids: dict[tuple[str, str, int], list[str]] = defaultdict(list)
    for refund in sorted(refunds, key=lambda r: (r.refunded_on, r.refund_id)):
        refund_ids[refund.provider, refund.attempt_id, refund.amount_minor].append(refund.refund_id)
    by_provider: dict[str, list[Contract]] = defaultdict(list)
    for row in contracts:
        by_provider[row.provider].append(row)

    result = FeeResult()
    unchecked: Counter[str] = Counter()
    for line in sorted(lines, key=lambda line: line.key):
        on = settled_on.get(line.settlement_key)
        if on is None:
            unchecked[SETTLEMENT_MISSING] += 1
            continue
        own = by_provider.get(line.provider, [])

        charged = line.fee_minor + line.tax_minor
        if line.line_type not in ("capture", "refund"):
            # A fee on its own, tied to no payment: only its type can be checked.
            in_force = [row for row in own if row.in_force(on)]
            if not in_force:
                unchecked[CONTRACT_MISSING] += 1
                continue
            result.checked += 1
            allowed = frozenset().union(*(c.allowed_fee_types for c in in_force))
            if line.line_type == "fee_adjustment" and line.fee_type not in allowed and charged > 0:
                result.differences.append(
                    _difference(
                        line, on, NOT_IN_CONTRACT, f"fee_type:{line.fee_type}", charged, charged, 0
                    )
                )
            continue

        # A capture or a refund: the contract is the one in force when the payment was made.
        if line.attempt_id is None:
            # Paid per order with no payment record, as a marketplace does:
            # the fee type names the contract row.
            method, network, card_type, paid_on = line.fee_type, None, None, on
        else:
            attempt = attempt_of.get(f"{line.provider}:{line.attempt_id}")
            if attempt is None:
                unchecked[PAYMENT_MISSING] += 1
                continue
            method, network, card_type, paid_on = (
                attempt.method,
                attempt.card_network,
                attempt.card_type,
                attempt.paid_on,
            )
        contract = contract_for(own, line.provider, method, network, card_type, paid_on)
        if contract is None:
            unchecked[CONTRACT_MISSING] += 1
            continue
        result.checked += 1

        if line.fee_type not in contract.allowed_fee_types and charged > 0:
            result.differences.append(
                _difference(
                    line, on, NOT_IN_CONTRACT, f"fee_type:{line.fee_type}", charged, charged, 0
                )
            )
        elif line.line_type == "capture":
            fee, tax = expected_fee(contract, line.gross_minor)
            over = charged - (fee + tax)
            if over > rules.tolerance(line.gross_minor):
                result.differences.append(
                    _difference(
                        line, on, ABOVE_CONTRACT, _cause_above(contract), over, charged, fee + tax
                    )
                )
        elif not contract.tax_on_refunded_fee:
            # The fee handed back is negative on a refund line; so is the tax returned with it.
            fee_returned, tax_returned = -line.fee_minor, -line.tax_minor
            tax_owed = bps_of(fee_returned, contract.tax_rate_bps) if fee_returned > 0 else 0
            kept = tax_owed - tax_returned
            if kept > rules.tolerance(line.gross_minor):
                waiting = (
                    refund_ids.get((line.provider, line.attempt_id, -line.gross_minor))
                    if line.attempt_id
                    else None
                )
                result.differences.append(
                    _difference(
                        line,
                        on,
                        TAX_ON_REFUNDED_FEE,
                        "tax_kept_on_refunded_fee",
                        kept,
                        tax_returned,
                        tax_owed,
                        waiting.pop(0) if waiting else None,
                    )
                )

    result.unchecked = dict(sorted(unchecked.items()))
    result.findings = group_differences(result.differences)
    return result


def group_differences(differences: Sequence[FeeDifference]) -> list[Detected]:
    """One claim-ready finding per provider, cause and month, keeping every line."""
    groups: dict[tuple[str, str, str], list[Item]] = defaultdict(list)
    for found in differences:
        groups[found.type, found.provider, found.cause].append(found.item())
    return [
        finding
        for (kind, provider, cause), items in sorted(groups.items())
        for finding in group(kind, provider, cause, items, RULES_VERSION)
    ]
