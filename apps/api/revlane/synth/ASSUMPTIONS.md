# Assumptions in the synthetic merchant

`make synth` writes provider-shaped files. Where a provider's public
documentation is unclear, or the handover documents leave a choice open, the
generator makes one and records it here. Mappers and their contract tests
(Milestone 3) must stay in step with this file.

## Scope and dates

- The period is the 90 days ending on the generation date (`--as-of`, default today).
- The fee audit covers the last full calendar month before the generation date.
  Generated on 16 November 2026 that is October, as on the prototype's screens.
- Every F1-F3 line has its transaction date and its settlement date inside that month.
- The F2 bookings are cancelled the day they are paid, so each payment and its
  refund sit in the same PayU settlement.
- R1 and R3 fall in the final seven days. R2 is the three most recent weekly
  marketplace payouts, so only the latest of them is dated in the final week.
- `--scale` shrinks the everyday volume only. The planted leaks and traps keep
  their exact counts and amounts at every scale.
- All amounts in the world are integer paise. Files carry them the way each
  provider writes them: Razorpay in paise, PayU as rupee strings, Cashfree and
  the CSV files as rupee decimals with two places.
- Shopper emails, phones, names, UPI ids and masked card numbers are invented
  (`@example.com`, `+9198...`). They are there so ingestion can prove it drops them.

## Fees

- Contract rates are in `contracts/fee_contracts.json`. A row with
  `card_network: null` covers every card network that has no row of its own.
- GST is 18% of the fee. Fees and tax are rounded half up to the paisa, line by line
  (`revlane.money.bps_of`).
- A payment line carries gross, fee and tax; its net is gross less fee less tax.
- A refund line reverses the payment: negative gross, and the original fee and tax
  as negative numbers, meaning they are handed back. This is a simplification;
  real providers differ on whether fees are returned.
- `tax_on_refunded_fee: false` in a contract means the tax on a refunded fee must
  come back with the fee. F2 is PayU returning the fee with tax of zero.
- F1 is measured as fee plus tax charged, less fee plus tax at the contract rate.
- F3 lines are settlement adjustments with `fee_type: instant_settlement`, which
  the contract's `allowed_fee_types` does not list. Their amount is fee plus tax.

## Settlement and the bank

- Each gateway settles a day's payments the next calendar day, weekends included.
  A day whose lines do not net to a positive amount pays nothing; its lines roll
  into the next payout.
- A settlement's bank credit lands the same day and carries the settlement's UTR
  in the reference column, except for traps T1 and T6, where the reference is blank.
- The marketplace pays weekly, for orders placed three to nine days before the payout.
- The bank statement holds credits only, plus a few unrelated credits that match nothing.

## Razorpay

- `payments.jsonl` is one snapshot per payment. `notes.booking_id` is the merchant's order id.
- `fee` on a payment includes tax, as in Razorpay's API; `tax` is the tax part.
- A payment that was authorised but never captured, then refunded by Razorpay, is
  written as `status: "refunded"`, `captured: false`, with a refund whose
  `notes.reason` is `payment_not_captured_auto_refund`. That is R1.
- An abandoned checkout is a payment left in `created`.
- UPI payments carry the payer's bank in `bank` (the real API does not always).
- `settlement_recon.jsonl` uses `entity_id` as the line reference, with `type`
  `payment`, `refund` or `adjustment`.
- `webhooks.jsonl` holds events for the last day and for trap T3 only.

## PayU

- `transactions.jsonl` is a stream of status reports. A transaction can appear more
  than once; `updated_on` orders the reports. Trap T4 is reported `pending`, then
  `failed` two days later.
- `udf1` is the merchant's order id; `txnid` is that id plus the attempt number.
- `settlements.csv` is one row per line, with the settlement's total repeated on
  each row; `line_id` is the line reference. `settlements_redelivery.csv` is one
  settlement delivered a second time (trap T2).

## Cashfree

- `order_id` is the merchant's order id.
- `refunds.jsonl` is not in the handover's file list; it was added because refunds
  need a source other than settlement events.
- `settlements.csv` is one row per event (`PAYMENT` or `REFUND`); `event_id` is the
  line reference.

## Marketplace

- `payouts.csv` has `LINE` rows (one per order) and one `PAYOUT` row per payout
  with the amount actually paid. R2 is a payout whose amount is below the sum of
  its lines' net amounts.

## Traps

- T7 uses status values that exist in no provider's vocabulary: `under_review`
  (Razorpay), `onHold` (PayU), `INCOMPLETE` (Cashfree).

## Not generated

- Mandates and disputes for the preview agents. The UPI incident and the ICICI
  card gap are present only as rough tilts in failure rates; nothing is tested
  against them.

## Reading the files back (Milestone 3)

- Status words were checked against each provider's documentation on 16 November 2026;
  the links are in `revlane/connectors/statuses.py`.
- Razorpay `refunded` is not in the design's mapping table. It is read as `succeeded`
  when the payment was captured and as `auto_refunded` when it never was.
- Razorpay refunds an authorised payment that is not captured within 3 days.
- Cashfree `CANCELLED` is read as `auto_refunded` on its own: a snapshot may never
  show the success that came before it. Cashfree also documents `VOID`, which the
  design's table does not list, so it maps to `unknown`.
- A snapshot has no time for its status. The payment's own time is used
  (`created_at`, `updated_on`, `payment_time`).
- The error codes behind each failure reason are the generator's own choice for PayU
  and Cashfree; they have not been checked against live responses.
- A Razorpay refund is `automatic` when its note says `payment_not_captured_auto_refund`.
- Capture and refund lines get `fee_type: payment`; marketplace lines get `commission`.

## Matching and the fee check (Milestone 4)

- Each gateway's settlement cycle is taken as one calendar day, as generated above.
  A payment is late once the cycle and two working days (Monday to Friday, no
  holiday calendar) have passed with no settlement line.
- A bank credit matches a settlement on amount when it lands on the settlement
  date or up to three calendar days after it.
- "₹1 per 1,000 lines" is read as ₹1 for every started 1,000 lines, so a small
  group still has ₹1 of tolerance. The same tolerance applies to a payout against
  the sum of its lines.
- "0.05% per line" in the fee tolerance is 0.05% of the line's gross amount.
- A fee is checked on fee and tax together: what the line carries against what
  the contract gives.
- A marketplace line has no payment behind it. Its contract row is the one whose
  method equals the line's fee type (`commission`), in force on the payout date.
- A fee adjustment with no payment behind it is checked only on its fee type,
  against every fee type the provider's contracts allow on the settlement date.
- Findings are grouped by type, provider, cause and calendar month. Reconciliation
  findings use the payment date (the payout date for a short payout); fee findings
  use the settlement date.
- Every finding starts as `recoverable`, as the answer key expects.
