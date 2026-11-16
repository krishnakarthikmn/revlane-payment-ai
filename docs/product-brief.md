# Product brief (condensed)

## The pitch

Revlane shows a merchant what happened to every rupee between checkout and the bank account, across every payment provider, and recovers what can be recovered. It is neutral: it works for the merchant, takes no money from any gateway, and can check a gateway's own fees.

## Who it is for first

The finance lead at a mid-market online merchant processing ₹50–500 Cr a year through two or three gateways, plus marketplaces and cash on delivery.

## The problem

| Leak | Kind | How recoverable |
| --- | --- | --- |
| Settled short | Money owed | High: a claim to the provider |
| Fees above contract | Money owed | High: a claim to the provider |
| Paid, then auto-refunded | Money owed | Medium |
| Chargebacks lost | Money owed | Medium |
| Failed renewals | Sale to win back | Medium |
| Failed payments | Sale to win back | Low to medium |
| Abandoned checkouts | Sale to win back | Low |

It stays hidden because each provider shows only its own slice, no single team owns the total, and gateway tools cover only their own rails.

## Three numbers, never mixed

- **Lost:** everything that leaked.
- **Recoverable:** the part someone can still act on.
- **Recovered:** money that reached the bank.

Revlane reports all three separately. The product must never present "lost" as if it were recoverable.

## What the first version must prove

That Revlane finds money a multi-gateway merchant did not know it was owed, shows the evidence, and drafts the claim.

- A free audit of the last 90 days.
- Reconciliation across gateways, marketplace and bank.
- A fee audit against the merchant's contracts.
- A chat that answers from the merchant's own data, with sources.
- A person approves everything. Nothing is sent on the merchant's behalf.

## What comes later

Recovery of failed payments, renewals and disputes; customer-built agents and workflows; any system connected through an API or MCP server. These appear in the console as previews.

## Promises the product makes

- Read-only by default, on the merchant's own credentials.
- No card numbers and, at first, no shopper contact details.
- Every figure traces to a source record.
- One merchant's data is never used for another without opt-in.
- Only merchants pay Revlane.

## The one measure

Rupees recovered per merchant per month: money that reached the bank because of Revlane.
