# Revlane

Revlane connects a merchant's payment gateways, bank accounts and order data, maps them onto one payments model, and finds money the merchant is owed. AI agents explain each finding and draft the claim; a person approves everything.

Read `docs/build-plan.md` before starting any work. Work one milestone at a time.

## Documents

- `docs/build-plan.md`: milestones and acceptance tests. The source of truth for what to build next.
- `docs/technical-design.md`: architecture, data model, engines, harness, chat, safety, security.
- `docs/design-spec.md`: screens, tokens, copy, sample data, and which screens are real or preview.
- `docs/synthetic-merchant.md`: test data and the answer key.
- `docs/product-brief.md`: product context.
- `design/prototype.dc.html`: the prototype source. Copy markup, styles and copy from it; do not redesign.

## Stack

- `apps/api`: Python 3.12, FastAPI, SQLAlchemy 2, Alembic, pytest.
- `apps/web`: TypeScript, Next.js (App Router), Tailwind CSS, Vitest, Playwright.
- Postgres 16, one database: operational data, analytics and the job queue. Row-level security on every tenant table.
- Object storage: local folder in development, behind a small storage interface.
- Language model: one provider's API behind `apps/api/revlane/llm/client.py`. A deterministic stub is the default in tests and when no key is set.
- Docker Compose for local development. A `Makefile` wraps every command.

Do not add Temporal, Kafka, ClickHouse, DuckDB or a model gateway. The technical design says when each would be added; that time is not now.

## Commands

Create these in milestone 0 and keep them working:

- `make dev`: start Postgres, API and web.
- `make test`: all backend and frontend unit tests.
- `make e2e`: Playwright tests.
- `make eval`: the agent and chat evaluation suite, in stub mode.
- `make synth`: generate the synthetic merchant into `data/synth/`.
- `make migrate`: apply migrations.
- `make lint`: ruff, mypy, eslint, tsc.

## Rules that must never be broken

1. **Money is integer minor units (paise).** Never floats. Columns end in `_minor`. Format only at the edge.
2. **The model never produces a number.** Model output refers to tool results by id; code inserts the values. Output containing a bare figure fails validation.
3. **The model never writes SQL.** Chat produces a structured query spec from the semantic layer; code compiles it.
4. **Every tenant table has `tenant_id` and row-level security.** Every query runs in a tenant-scoped session. Add an isolation test with each new table.
5. **Nothing is sent to a provider or a customer.** The first version drafts claims and queries; a person sends them. Do not implement outbound messaging or provider write calls.
6. **Every proposal needs approval.** No code path may execute an action without an `approval` row.
7. **Raw payloads are kept; card numbers and contact details are not.** Drop card numbers at ingestion. Shoppers are keyed-hash tokens only.
8. **No secrets in code, logs, prompts or fixtures.** Use `.env` and the vault interface.
9. **Status is derived from `attempt_event`, never stored as the only truth.** Derivation rules are versioned code with tests.
10. **Only sandbox or synthetic data.** Never call a provider's production API. Never use real customer data.

## How to work

- Write the tests for a milestone's acceptance criteria first, then the code.
- Engines (mapping, status derivation, matching, fees) are pure functions over data, tested against the synthetic merchant's answer key at 100% exact.
- Keep changes small: one pull request per milestone, or per numbered task inside a milestone.
- After each milestone, run `make lint test e2e eval`, then stop and summarise: what was built, what was skipped, and any decision you made that the documents did not cover.
- If the documents conflict, the order of authority is: this file, then `docs/build-plan.md`, then `docs/technical-design.md`, then `docs/design-spec.md`. Say which conflict you found.
- If something needs a decision listed in `docs/decisions-needed.md` and it is still unanswered, use the default written there and note it in your summary.
- Ask before adding a dependency that is not already in the stack list.

## Conventions

- Python: type hints everywhere, `ruff` and `mypy --strict` clean, no bare `except`.
- TypeScript: strict mode, no `any`.
- API: REST under `/api/v1`, JSON, cursor pagination, errors as `{ "error": { "code", "message" } }`.
- Database: snake_case, UUID primary keys unless a provider id is the natural key, `created_at` and `updated_at` on every table.
- Names: use the canonical vocabulary from the technical design (`payment_attempt`, `settlement_line`, `bank_credit`, `match_link`, `finding`, `claim`). Do not invent synonyms.
- UI copy: plain English, amounts in lakh format as in the prototype (`₹22.6L`), never "leverage" or "seamless".
