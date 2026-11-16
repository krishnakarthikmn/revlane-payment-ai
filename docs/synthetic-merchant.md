# The synthetic merchant

All development, tests, evaluation and demos run on one fictional merchant. `make synth` generates it deterministically from a seed, in each provider's own file and payload shape, together with an answer key the engines are tested against.

## Profile

- **Name:** Acme Travel. Online travel bookings plus an "Acme Plus" membership.
- **Period:** 90 days ending on the generation date. The "last 7 days" are the final week.
- **Volume:** about 4,000 payment attempts a day; average booking about ₹10,500; daily volume about ₹4.2 Cr.
- **Gateways:** Razorpay (about 52% of volume), PayU (33%), Cashfree (15%).
- **Other sources:** one marketplace paying out weekly; one HDFC Bank current account; a store order export.
- **Methods:** UPI 55%, cards 35%, netbanking 8%, other 2%. The generator sizes the volume behind each planted leak to hit the amounts below exactly (for example, enough Amex volume on Cashfree for a 0.15% overcharge to total ₹1,60,000), and adjusts the rest of the mix around it.

## What the generator writes

Under `data/synth/<seed>/`:

| Path | Contents |
| --- | --- |
| `razorpay/payments.jsonl`, `settlements.jsonl`, `settlement_recon.jsonl`, `refunds.jsonl`, `webhooks.jsonl` | Records in Razorpay's shape and status vocabulary |
| `payu/transactions.jsonl`, `settlements.csv`, `refunds.jsonl` | Records in PayU's shape, including `unmappedstatus` |
| `cashfree/orders.jsonl`, `payments.jsonl`, `settlements.csv` | Records in Cashfree's shape |
| `marketplace/payouts.csv` | Weekly payout file |
| `bank/hdfc_statement_<month>.csv` | Credits with UTR, date, amount, narration |
| `orders/bookings.csv` | Order id, amount, status, fulfilment |
| `contracts/fee_contracts.json` | Agreed rates by provider, method, card type, effective date |
| `answer_key.json` | Every planted leak: type, record ids, amount in paise, expected finding state |

Field names and status values follow each provider's public documentation, linked in `docs/technical-design.md`. Where the documentation is unclear, pick a shape, record the assumption in `data/synth/ASSUMPTIONS.md`, and keep the mapper's contract tests in step.

## Planted leaks

Amounts are fixed so the engines reproduce the numbers on the prototype's screens for the last 7 days.

### Reconciliation: ₹3.1L in total

| # | Leak | Detail | Amount |
| --- | --- | --- | --- |
| R1 | Paid, then auto-refunded | 40 bookings where the payment succeeded, the order never confirmed, and the provider refunded automatically | ₹1,30,000 |
| R2 | Marketplace payouts short | 3 weekly payouts, each lower than the sum of their lines | ₹1,20,000 |
| R3 | Captured, not settled | 22 succeeded payments with no settlement line after the cycle plus grace period | ₹60,000 |

### Fee audit: ₹2.6L in total, across 1,912 settlement lines

| # | Leak | Detail | Amount |
| --- | --- | --- | --- |
| F1 | Rate above contract | Cashfree charges 2.15% on Amex against a 2.00% contract | ₹1,60,000 |
| F2 | Tax on refunded fees | PayU keeps GST on fees for payments that were refunded | ₹60,000 |
| F3 | Fee not in contract | Razorpay charges an instant-payout fee the contract does not allow | ₹40,000 |

The fee audit covers the calendar month, as in the prototype ("₹2.6L · October"); the generator spreads F1–F3 over that month.

### Traps the engines must not fall for

| # | Trap | Expected behaviour |
| --- | --- | --- |
| T1 | One bank credit covers three settlements | Linked as a group; no finding |
| T2 | One settlement file delivered twice | Ingested once; no double counting |
| T3 | A payment that fails, then succeeds on retry within 10 minutes | One order, two attempts; not a leak |
| T4 | A status that reverses late (pending to failed after two days) | Status history updated; derived status changes |
| T5 | A refund netted against a later payout | Matched; no "settled short" finding |
| T6 | A settlement that lands in the bank two days late across a weekend | Matched inside the date window |
| T7 | An unknown provider status value in 5 records | Mapped to `unknown`, alert raised, counted in connector health |

### Planted for the preview agents (not tested in the first version)

- A UPI timeout incident: HDFC Bank customers on Razorpay, Tuesday 2–4 pm, success down 9.1 points, ₹9.4L lost.
- ICICI credit cards succeeding 6.2 points more often on PayU than on Razorpay.
- 212 failed membership auto-debits: 129 insufficient funds, 48 expired card, 35 mandate revoked.
- 5 disputes due within 48 hours, three with proof of travel.

## Answer key format

```json
{
  "seed": 42,
  "generated_on": "2026-11-16",
  "leaks": [
    {
      "id": "R1",
      "type": "paid_auto_refunded",
      "provider": "razorpay",
      "record_ids": {"orders": ["BK-10231"], "attempts": ["pay_Abc123"]},
      "amount_minor": 325000,
      "expected_state": "recoverable"
    }
  ],
  "totals_minor": {"reconciliation": 31000000, "fee_audit": 26000000},
  "traps": [{"id": "T1", "record_ids": {"bank_credits": ["UTR123"]}, "expect": "no_finding"}]
}
```

## Acceptance

- Two runs with the same seed produce byte-identical files.
- The matching engine reports exactly R1–R3 with the answer key's record ids and amounts, and nothing for T1–T6.
- The fee check reports exactly F1–F3, totalling ₹2,60,000 over 1,912 lines.
- Re-ingesting every file a second time changes no totals.
