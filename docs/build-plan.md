# Build plan

Nine milestones. Each ends with passing acceptance tests and a written summary. Do them in order; do not start one until the previous one is accepted.

The first milestone that shows something is Milestone 1: the whole console running on sample data. From there, real engines replace sample data one surface at a time.

## Repository layout

```
apps/
  api/                 FastAPI service
    revlane/
      api/             routes
      db/              models, migrations, tenant session
      connectors/      Connector protocol, razorpay/, payu/, cashfree/, files/
      engine/          status.py, matching.py, fees.py, findings.py
      harness/         run loop, tools, playbooks/, approvals, audit
      chat/            semantic layer, query spec, compiler
      llm/             client.py, stub.py
      synth/           synthetic merchant generator
      fixtures/        acme_travel.json (sample data for preview surfaces)
    tests/
  web/                 Next.js console
    app/               chat, agents, workflows, approvals, connections
    components/
    e2e/
data/                  generated files, never committed
docs/
design/
```

## Milestone 0: scaffold

**Build**

- Monorepo as above, Docker Compose with Postgres 16, `Makefile` with every command listed in `CLAUDE.md`.
- API health route; web page that calls it.
- Linting, type checking, test runners, and a CI workflow that runs `make lint test`.
- `.gitignore` covering `.env` and `data/`.

**Accepted when**

- `make dev` starts everything and the web page shows the API's health response.
- `make lint test` passes in CI.

## Milestone 1: the console on sample data

**Build**

- Move the prototype's sample data into `fixtures/acme_travel.json`; serve it from `/api/v1/fixtures`.
- Port the prototype to Next.js and Tailwind, following `docs/design-spec.md`: shell, sidebar (pinned, collapsed, hover), header, notifications, and all five views.
- Chat with the composer, "+" menu, attachments, thinking state, and every rich block. Replies come from the fixtures using the prototype's keyword routing.
- Agent grid, agent detail, rules editor, autonomy control, approvals with Approve, Reject and Undo, workflows and routines, connections and the custom MCP panel.
- State is in memory per session, as in the prototype.

**Accepted when**

- A Playwright test walks every view and every suggestion chip, approves and undoes a fix, saves a routine and a skill, and connects the sample MCP server.
- A side-by-side check against the prototype finds no differences in copy, colours, spacing or component structure. List any you could not match.
- Lighthouse accessibility score of 95 or more on the chat view.

## Milestone 2: schema and synthetic merchant

**Build**

- Migrations for every entity in `docs/technical-design.md`: setup, money movement, links, output, identity. `tenant_id` and row-level security on each. A tenant-scoped session helper.
- The generator described in `docs/synthetic-merchant.md`, behind `make synth`.
- A vault interface with a local development backend.

**Accepted when**

- Two generator runs with the same seed are byte-identical; the answer key totals match the document.
- An isolation test proves a session for tenant A cannot read tenant B's rows in any table.
- Migrations apply and roll back cleanly.

## Milestone 3: connectors and status

**Build**

- The `Connector` protocol; mappers for Razorpay, PayU and Cashfree records; file connectors for marketplace payouts, bank CSV and order export.
- Ingestion that is idempotent on provider id, keeps the raw payload, drops card numbers, and writes `attempt_event` rows.
- Status derivation from events plus clock rules, as versioned code.
- Upload endpoints and a connector health summary (last sync, record counts, unmapped statuses).
- Live sandbox calls for the two chosen gateways behind `PROVIDER_LIVE_CALLS`, off by default.

**Accepted when**

- Contract tests pin every status in the mapping table for each provider.
- The synthetic merchant ingests with zero unknown statuses except the five planted in trap T7, which raise an alert.
- Ingesting everything twice changes no row counts.
- Trap T4 (late reversal) changes the derived status without losing history.

## Milestone 4: matching and fee engines

**Build**

- Three-way match: order to attempt, attempt to settlement line, settlement to bank credit, including group matching of several settlements to one credit. Configurable grace period, date window and tolerance.
- `match_link` rows for every link; findings with type, amount, cause, provider, evidence ids and state.
- Fee contracts: API and form. Fee check per settlement line, grouped into claim-ready findings.
- Finding lifecycle: `lost`, `recoverable`, `claimed`, `recovered`, `written_off`, with transitions recorded.

**Accepted when**

- Against the answer key: R1–R3 and F1–F3 are found with the exact record ids and amounts; reconciliation totals ₹3,10,000 and fees ₹2,60,000 over 1,912 lines.
- Traps T1, T2, T3, T5 and T6 produce no finding.
- Every engine function is pure and covered by tests on hand-computed fixtures.

## Milestone 5: harness, agents and approvals

**Build**

- Playbooks as YAML; the run loop; a tool registry with allow-lists; checkpointed runs on a Postgres-backed queue; idempotency keys; one active run per tenant per playbook.
- The model client with a deterministic stub. Output schema validation and grounding by reference: the model cites tool-result ids and code inserts the figures.
- Three playbooks: leakage audit, reconciliation, fee audit. Each produces a finding summary and a proposal.
- Approval gate, two-person approval flag, append-only audit log.
- Claim drafts: a PDF and an email-text export built from the finding's evidence. Nothing is sent.
- `make eval`: the evaluation suite from the technical design, running in stub mode.

**Accepted when**

- The leakage audit names every planted leak with the right amount.
- An output containing a bare figure is rejected by validation.
- No action can be created without an approval; a test tries and fails.
- A prompt-injection test (instructions hidden in a bank narration and an uploaded file) changes nothing.
- `make eval` passes at the bars in the technical design.

## Milestone 6: Ask Revlane

**Build**

- The semantic layer as versioned YAML; a structured query spec; a compiler from spec to SQL against a read-only, tenant-scoped view with row and cost limits.
- A chat endpoint that streams the steps, the answer, a table or chart, and the query ids.
- Refusal when a question needs a metric that is not defined.
- A fixed question set with SQL ground truth, added to `make eval`.

**Accepted when**

- At least 90% of the question set is answered correctly in stub mode; the live-model run is reported separately when a key is set.
- No path lets model text reach the database as SQL; a test asserts this.
- A cross-tenant question returns nothing from another tenant.

## Milestone 7: wire the console to the real backend

**Build**

- Replace sample data with live API calls for every surface marked Real in `docs/design-spec.md`.
- Add the "Preview" pill to every surface that stays on sample data.
- Rules editor and "Simulate on 90 days" for the two real agents, replaying the engines with the edited thresholds.
- Autonomy control limited to Observe and Suggest.
- Notifications generated from finding and proposal events.
- A first-run flow: connect a gateway or upload files, enter fee contracts, run the audit.

**Accepted when**

- An end-to-end test does this without touching sample data: load the synthetic merchant, run the audit, open the Reconciliation agent, approve the proposal, download the claim, ingest a later bank file that contains the recovered money, and see the finding move to "recovered".
- Asking "Where did we lose the most last week?" shows the engine's reconciliation and fee numbers, matching the prototype's ₹3.1L and ₹2.6L.

## Milestone 8: hardening

**Build**

- Audit-log export; retention and erasure jobs; offboarding that revokes and deletes credentials.
- Rate limits, request size limits, upload type checks.
- Tracing per run; metrics for sync lag, unmapped statuses, findings and model spend.
- A security checklist run against the rules in `CLAUDE.md`, with a test for each rule where one is possible.
- Infrastructure code as a stub for one cloud region; a deployment guide.
- `docs/RUNBOOK.md`: how to run, seed, test, add a connector, add a metric, add a playbook.

**Accepted when**

- Every rule in `CLAUDE.md` has at least one automated test or a written reason why not.
- A fresh clone reaches a working demo with three commands, documented in the runbook.

## Out of scope for this build

Outbound messaging, provider write calls, failed-payment recovery, holdout measurement, the workflow engine, the builder, metering and credits, business-system connectors, custom MCP execution, SSO, mobile layouts. Their screens exist as previews only.
