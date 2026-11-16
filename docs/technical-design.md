# Technical design

Revlane connects a merchant's payment providers, bank accounts and order data, maps them onto one payments model, and finds money the merchant is owed. This is the design for the first version. Later phases are outlined at the end.

## 1. Scope

| In the first version | Not in the first version |
| --- | --- |
| Connectors for two gateways, plus bank-statement and order-file upload | Contacting shoppers; failed-payment recovery |
| The unified model with status history | Automatic mode for any agent |
| The matching and fee engine | Workflow engine and builder |
| Three agents: leakage audit, reconciliation, fee audit | Usage credits and metering |
| Ask Revlane, a chat over the merchant's own data | Business-system connectors |
| Approval flow, audit log, console | Slack, Teams and WhatsApp surfaces |

**Sizing.** A merchant processing ₹500 Cr a year makes roughly ten thousand payment attempts a day. Twenty such merchants produce a few hundred thousand rows a day. One Postgres database carries that.

## 2. Principles

1. **One data model, many providers.** Agents never see provider-specific fields; connectors translate.
2. **Code computes, the model explains.** Every number comes from deterministic, tested code.
3. **Declarative agents.** A playbook is versioned configuration that the harness runs.
4. **Approval before autonomy.** Anything that would leave Revlane waits for a person.
5. **Proof that fits the leak.** A claim is proven when the money arrives in the bank.
6. **The merchant's own credentials.** Revlane never holds funds.

## 3. Architecture

Six layers, top to bottom:

1. **Surfaces:** the console and chat.
2. **API and auth:** sessions, roles (viewer, approver, admin), approvals, webhook intake.
3. **Intelligence:** Ask Revlane (semantic layer) and the agent harness (run loop, limits, approval gate).
4. **Engines and services:** the matching and fee engine (every number, in code); the action router (drafts only in the first version); model access (one provider behind a thin client).
5. **Unified payments model:** orders, attempts, events, settlements, bank credits, links, findings.
6. **Connectors:** gateways, bank statements, order files.

**One finding, end to end:** ingest → normalise → match → finding → proposal (agent drafts the claim) → approval → claim (the merchant sends it) → recovered (seen in a later bank credit). A rejected proposal is dismissed with its reason recorded. Only the proposal step and its explanation involve the language model.

## 4. Data model

All tables carry `tenant_id UUID NOT NULL`, `created_at`, `updated_at`, and row-level security. Amounts are `BIGINT` in minor units with the suffix `_minor`. Provider records keep `raw JSONB`.

### Setup

| Table | Key columns |
| --- | --- |
| `tenant` | `id`, `name`, `region`, `policy JSONB` |
| `provider_account` | `id`, `provider` (razorpay, payu, cashfree, marketplace, bank, orders), `account_ref`, `credential_ref`, `status`, `last_sync_at` |
| `fee_contract` | `id`, `provider_account_id`, `method`, `card_network`, `card_type`, `rate_bps`, `fixed_fee_minor`, `tax_rate_bps`, `tax_on_refunded_fee` (bool), `allowed_fee_types TEXT[]`, `effective_from`, `effective_to` |

### Money movement

| Table | Key columns |
| --- | --- |
| `order` | `id`, `merchant_order_id`, `amount_minor`, `currency`, `status`, `fulfilment_status`, `placed_at` |
| `payment_attempt` | PK (`tenant_id`, `provider`, `attempt_id`); `provider_account_id`, `merchant_order_id`, `amount_minor`, `currency`, `method`, `instrument_bank`, `card_network`, `card_type`, `upi_app`, `customer_token`, `first_seen_at`, `raw` |
| `attempt_event` | `id`, `provider`, `attempt_id`, `provider_status`, `provider_error_code`, `provider_stage`, `occurred_at`, `ingested_at`, `source` (api, webhook, file), `raw`. Append-only. |
| `refund` | `id`, `provider`, `attempt_id`, `refund_id`, `type` (merchant, automatic), `amount_minor`, `status`, `occurred_at` |
| `settlement` | `id`, `provider`, `settlement_id`, `gross_minor`, `fee_minor`, `tax_minor`, `net_minor`, `utr`, `settled_on` |
| `settlement_line` | `id`, `provider`, `settlement_id`, `attempt_id`, `line_type` (capture, refund, chargeback, reversal, fee_adjustment), `gross_minor`, `fee_minor`, `tax_minor`, `net_minor`, `fee_type`, `raw`. Unique on (`provider`, `settlement_id`, provider line reference). |
| `bank_credit` | `id`, `account_ref`, `utr`, `amount_minor`, `credited_on`, `narration`, `raw` |
| `dispute`, `mandate` | Created now, populated for preview data only |

### Links, output, identity

| Table | Key columns |
| --- | --- |
| `match_link` | `id`, `left_type`, `left_id`, `right_type`, `right_id`, `rule`, `confidence`, `amount_minor`, `created_by` (engine, user), `active` |
| `finding` | `id`, `type` (paid_auto_refunded, settled_short, captured_not_settled, fee_above_contract, tax_on_refunded_fee, fee_not_in_contract), `provider`, `amount_minor`, `cause`, `state` (lost, recoverable, claimed, recovered, written_off), `evidence JSONB` (record ids), `period_start`, `period_end`, `rule_version` |
| `finding_event` | `id`, `finding_id`, `from_state`, `to_state`, `reason`, `actor`, `occurred_at` |
| `proposal` | `id`, `finding_ids UUID[]`, `playbook`, `playbook_version`, `action_type`, `summary JSONB` (text with tool-result references), `status` (pending, approved, rejected, expired) |
| `approval` | `id`, `proposal_id`, `approver_id`, `decision`, `reason`, `decided_at` |
| `claim` | `id`, `proposal_id`, `provider`, `amount_minor`, `document_ref`, `status` (drafted, sent_by_merchant, paid, refused), `provider_reply` |
| `audit_log` | `id`, `actor`, `kind` (connector_call, model_call, tool_call, approval, action), `payload_hash`, `ref`, `occurred_at`. Append-only. |
| `customer_token` | Not a table in the first version: a column holding a keyed hash |

### Four decisions

- **Status is a history, not a column.** The canonical status of an attempt is a function of its `attempt_event` rows and clock rules. It is exposed through a view or materialised table that can be rebuilt when a rule changes.
- **Links are explicit.** One bank credit can cover several settlements; `match_link` records each pairing so it can be shown and undone.
- **A finding is a first-class record** with its evidence and a state.
- **Shopper identity is a keyed hash** of the normalised phone or email under a per-tenant key. No contact details are stored in the first version.

### Canonical status mapping

| Canonical | PayU (`unmappedstatus`) | Razorpay | Cashfree (`payment_status`) | Derived from time? |
| --- | --- | --- | --- | --- |
| `succeeded` | `captured` | `captured` | `SUCCESS` | No |
| `authorized` | `auth` | `authorized` | none | No |
| `failed` | `failed`, `dropped` | `failed` | `FAILED` | No |
| `pending` | `pending`, `in progress` | `created` (recent) | `PENDING`, `NOT_ATTEMPTED` | No |
| `abandoned` | `bounced`, `userCancelled`, stale `initiated` | stale `created` | `USER_DROPPED` | Yes, for the stale cases (default: 30 minutes) |
| `auto_refunded` | `autoRefund` | `authorized`, not captured in time | `CANCELLED` after a late success | Yes, for Razorpay |
| `held_for_review` | none | none | `FLAGGED` | No |
| `unknown` | anything else | anything else | anything else | No; raises an alert |

Sources, carried over from the first draft and not re-verified: [PayU payment states](https://docs.payu.in/docs/payment-state-explanations), [Razorpay payments](https://razorpay.com/docs/payments/payments.md), [Cashfree enums](https://www.cashfree.com/docs/api-reference/payments/enums.md). Check each against the provider's documentation when writing the mapper, and pin it with a contract test.

Failure reasons mapped at ingestion: `bank_declined`, `insufficient_funds`, `auth_failed`, `auth_abandoned`, `technical_timeout`, `risk_blocked`, `user_cancelled`, `unknown`.

## 5. Connectors

```python
class Connector(Protocol):
    def authorize(self, tenant) -> CredentialRef: ...
    def backfill(self, since: date) -> Iterator[RawRecord]: ...
    def poll(self, cursor) -> Iterator[RawRecord]: ...
    def handle_webhook(self, payload, signature) -> list[RawRecord]: ...
    def map(self, raw: RawRecord) -> list[CanonicalRecord]: ...
    def actions(self) -> list[ActionSpec]: ...        # empty in the first version
    def execute(self, action: ApprovedAction) -> ActionResult: ...  # not implemented in the first version
```

| Source | Read | Notes |
| --- | --- | --- |
| Razorpay | Payments, orders, refunds, settlements, settlement recon; webhooks | [MCP tools reference](https://razorpay.com/docs/mcp-server/tools-reference.md) lists no dispute tools |
| PayU | Transactions, settlements, refunds, payouts; webhooks | [Remote MCP server](https://docs.payu.in/docs/payu-remote-mcp-server-integration); responses redact personal data |
| Cashfree | Orders, payments with failure reasons, refunds, settlements | File-based in the first version |
| Marketplace | Payout files (CSV) | File-based |
| Bank | Statement upload, CSV first, PDF after | Match by UTR where present |
| Orders | File upload or store export | |

- **Mappers are pure:** raw record in, canonical records out. All provider knowledge lives here.
- **Ingestion is idempotent** on the provider's own id and safe to repeat.
- **Sync:** webhooks, polling every 15 minutes, and a nightly backfill. Each record carries `source` and `ingested_at`. Backfills respect rate limits and resume from a cursor.
- **Fee contracts** are entered by a person in a form in the first version.
- **Live provider calls** are sandbox-only and off by default.

## 6. Matching and fee logic

Both engines are deterministic code, tested against the synthetic merchant at 100% exact.

### Three-way match

1. **Order to attempt.** Join on the merchant's order id. A succeeded attempt whose order is cancelled or unconfirmed, with an automatic refund, is `paid_auto_refunded`.
2. **Attempt to settlement line.** Join on the provider's payment id. A succeeded attempt with no settlement line after the provider's cycle plus a grace period is `captured_not_settled`.
3. **Settlement to bank credit.** Join on UTR where present; otherwise on amount and date within a window. If no single credit matches, search for a group of settlements from the same provider, in the window, that sums to one credit.
4. **Payout to lines.** A settlement or marketplace payout whose net is lower than the sum of its lines, beyond tolerance, is `settled_short`.
5. **Residuals become findings** with their evidence ids.

| Rule | Starting value |
| --- | --- |
| Settlement grace period | Provider cycle plus 2 working days |
| Date window for bank match | 3 days |
| Amount tolerance | Zero for single matches; ₹1 per 1,000 lines for groups |
| Auto-link | Exact reference matches only; fuzzy matches are shown for review |

Refunds and chargebacks that net against a later payout are matched to that payout before any shortfall is reported.

### Fee check

For each settlement line, recompute the expected fee from the `fee_contract` in force on the transaction date: `rate_bps × gross`, plus any fixed fee, plus tax on the fee.

- Charged more than expected, beyond tolerance: `fee_above_contract`.
- Tax retained on the fee of a refunded payment, where the contract says it should be returned: `tax_on_refunded_fee`.
- A fee type the contract does not allow: `fee_not_in_contract`.

Group by provider, cause and month into one claim-ready finding, keeping every line id as evidence. Tolerance: ₹1 or 0.05% per line, whichever is larger.

## 7. Agents and harness

An agent is a playbook:

```yaml
playbook: fee_audit
version: 1
trigger:
  - schedule: "0 6 * * *"
read_tools: [fee_differences, contract_for, settlement_lines]
actions:
  - type: draft_fee_claim
    approval: required
    limits: {min_claim_minor: 100000, max_claims_per_run: 20}
output_schema: FindingAndProposal_v1
escalate_if: [contract_missing, confidence < 0.8]
```

**Run loop**

1. Assemble context: system rules and playbook, tenant policy, past approvals and rejections for this playbook, the trigger and a compact data summary.
2. Call the model with only the tools the playbook allows.
3. For each tool call, check the allow-list, schema and tenant scope. Reads run; writes become proposals.
4. Log every call and result.
5. Validate the output against its schema, then check grounding.
6. Persist, or escalate if the budget runs out.

**Grounding by construction.** Output text contains references such as `{{t3.total_minor}}`, never digits for amounts, counts or percentages. Code resolves each reference against the tool results of that run and formats it. Validation rejects any amount-like literal in model text.

**Reliability.** Checkpoint after each tool call; idempotency key on every proposal (tenant, playbook, finding set, period); one active run per tenant per playbook; connector calls retry three times with backoff; invalid output retries once.

**Models.** One mid-tier model plans, explains and drafts. Rules classify wherever they can. The stub client returns fixed, schema-valid outputs keyed by playbook and tool results, so tests and `make eval` run without a key.

**First-version tools:** `three_way_match`, `findings_for`, `settlement_lines`, `bank_credits`, `fee_differences`, `contract_for`, `rupee_impact`, `evidence_for`.

**First-version playbooks:** `leakage_audit` (daily summary of lost, recoverable and recovered by cause and provider), `reconciliation` (proposes settlement queries and re-offer lists), `fee_audit` (proposes fee claims).

## 8. Ask Revlane

1. **Understand.** The model maps the question to metrics, dimensions, filters and a time range from the semantic layer, and returns a query spec.
2. **Compile.** Code turns the spec into SQL against a read-only, tenant-scoped view, with row and cost limits.
3. **Analyse.** For "why" questions it calls the same tools the agents use.
4. **Answer.** Text with references, a table or chart, and the query ids. An undefined metric gets a refusal that names what is missing.
5. **Act.** It may offer a next step, which goes through the approval flow.

```yaml
metric: payment_success_rate
description: Share of payment attempts that succeeded, excluding abandoned attempts
numerator: count(payment_attempts where status = 'succeeded')
denominator: count(payment_attempts where status in ('succeeded','failed'))
dimensions: [provider, method, instrument_bank, card_network, upi_app, hour]
```

First-version metrics: `payment_volume_minor`, `payment_success_rate`, `lost_minor`, `recoverable_minor`, `recovered_minor`, `fee_effective_rate`, `fee_overcharge_minor`, `unsettled_minor`, `match_rate`, `refund_amount_minor`.

## 9. Safety

| Evaluation layer | Tests | Pass bar |
| --- | --- | --- |
| Connector mapping | Provider samples map to the right canonical status | 100% on fixtures |
| Matching and fees | Every rule against hand-computed fixtures and the answer key | 100% exact |
| Diagnosis | Planted leaks in the synthetic merchant | Top cause correct in 90%; amounts exact |
| Grounding | Every figure is a reference to a tool result | Zero bare figures |
| Proposals | Right action, inside policy, right target | Zero policy violations |
| Chat | Fixed question set against SQL ground truth | 90% correct; refuses undefined metrics |
| Safety | Prompt injection through narrations, file contents and tool outputs | Zero exceedances |

**Guardrails in code, outside the model:** tool and action allow-lists per playbook and tenant; platform hard limits; the approval gate; all external text treated as untrusted data and never as instructions; kill switches per agent, per tenant and global.

**Autonomy levels:** 0 Observe, 1 Suggest, 2 Batch approve, 3 Auto within limits. The first version implements 0 and 1. The data model stores the level per agent per tenant.

## 10. Security and data lifecycle

| Area | Control |
| --- | --- |
| Tenant isolation | `tenant_id` on every row; row-level security; tenant-scoped sessions; isolation enforced in the query layer for chat |
| Credentials | KMS-backed vault in production, local encrypted backend in development; never in logs or prompts. Many gateway keys cannot be scoped; treat the vault as the highest-value target |
| Money | Revlane never holds or moves funds, and sends nothing to providers in the first version |
| Personal data | None kept; shoppers are keyed-hash tokens |
| Card data | Never stored; dropped at ingestion |
| Model providers | No personal data, card data or credentials in any prompt |
| Access | Roles for viewer, approver and admin; optional two-person approval |
| Audit | Append-only log, exportable |
| Lifecycle | Retention period per tenant; erasure of a token and its links on instruction; offboarding revokes and deletes credentials at once |

## 11. Stack

| Component | Choice | Add later, when |
| --- | --- | --- |
| Services | Python (FastAPI); TypeScript (Next.js) | |
| Database | One Postgres with row-level security, for operational data, analytics and the job queue | A column store when scans slow down |
| Durable runs | Postgres-backed job queue | Temporal, when workflows ship |
| Files | Object storage | |
| Secrets | KMS plus a secrets manager | |
| Model access | One provider behind a thin client | A gateway when a second provider is added |
| Hosting | One cloud, India region | |

## 12. Later phases, in outline

- **Recovery agents.** Failed-payment, renewal and chargeback agents act on the merchant's provider accounts after approval. They need webhooks, shopper contact in a separate vault, recorded consent and approved message templates. Proof is a per-shopper holdout of 10–15%; billing uses the lower bound of a 95% confidence interval.
- **Workflows.** A durable engine chains triggers, agents, customer tools and approvals.
- **Builder.** Configure, describe or code; all produce a versioned playbook; publishing goes through validate, simulate, test, approve.
- **More connectors and custom MCP.** An admin approves which tools agents may use; outputs are untrusted data.
- **Metering.** Credits per harness event, not per token.
