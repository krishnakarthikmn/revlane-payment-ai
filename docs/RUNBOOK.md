# Runbook

How to run Revlane, load data into it, test it and extend it.

## From a fresh clone to a working demo

You need Python 3.12 with [uv](https://docs.astral.sh/uv/), Node 24, and either Docker or a Postgres 16 server on `localhost:5432`.

```bash
cp .env.example .env
```

```bash
make install
```

```bash
make demo
```

`make demo` starts the database, applies migrations, loads a small synthetic merchant dated today, runs the audit, and then starts the API on port 8000, a worker, and the console on http://localhost:3000. It needs no model key: the agents and chat run on the deterministic stub.

Open the console and sign in as `priya.menon@acmetravel.example` with the password in `DEMO_USER_PASSWORD` in your `.env` (`change-me-demo-password` unless you changed it). Then try:

- the "Where did we lose the most?" chip on the chat page;
- Agents, then Reconciliation: approve the proposal and download the drafted claim;
- http://localhost:3000/contracts for the fee contracts the audit checks against.

Stop everything with Ctrl-C. Running `make demo` again leaves the loaded data as it is. If ports 8000 or 3000 are taken, use `make demo API_PORT=8100 WEB_PORT=3100`.

If Docker is not installed, `make db` (which `make demo` runs) prepares a local Postgres instead. It needs a `psql` that can create roles and databases.

## Everyday commands

| Command | What it does |
| --- | --- |
| `make dev` | Postgres, the API (port 8000), a worker, and the web app (port 3000) |
| `make migrate` | Apply database migrations |
| `make lint` | ruff, mypy, eslint, tsc |
| `make test` | Backend and frontend unit tests |
| `make e2e` | Playwright tests, on their own servers and database |
| `make eval` | The evaluation suite, in stub mode |
| `make eval-live` | The chat question set against the live model; needs `LLM_API_KEY` |
| `make synth` | Write the synthetic merchant's files to `data/synth/` |
| `make ingest` | Load `data/synth/` (or `ARGS=<folder>`) into the demo tenant |
| `make audit` | Run the matching and fee engines and print the findings |
| `make agent ARGS=<playbook>` | Run one playbook now: `leakage_audit`, `reconciliation`, `fee_audit` |
| `make worker` | Carry out runs queued through the API |
| `make user ARGS="add <email> '<name>' <role>"` | Add a person who can sign in; asks for the password |
| `make retention` | Delete records older than each tenant's retention period |
| `make offboard ARGS=<tenant id>` | Delete a tenant's credentials and stop its agents |

After each milestone or larger change, run `make lint test e2e eval`.

## Loading data

**The full-size synthetic merchant** (about 360,000 payments; a few minutes to load):

```bash
make synth
```

```bash
make ingest ARGS="--as-of 2026-11-16"
```

```bash
make agent ARGS="leakage_audit --as-of 2026-11-16"
```

Use the date the files were generated on for `--as-of`. `make synth ARGS="--scale 0.05 --as-of 2026-11-16"` writes a smaller one. Do not load two scales into one tenant: each is a separate set of records, so the planted leaks would be counted twice.

**Your own files.** In the console, Connections has an "Upload a file" button for each provider. Files are CSV or JSON lines, UTF-8, up to 25 MB. The file itself is not kept; its records are, without card numbers or contact details. Then enter the fee contracts at `/contracts` and run the audit from the chat page or an agent's page.

**Only sandbox or synthetic data.** Live provider calls are off unless `PROVIDER_LIVE_CALLS=true`, reach only sandbox hosts, and need test-mode keys.

## Testing

- Unit tests use a database called `revlane_test`, created on first run and rebuilt from migrations each session.
- Browser tests start their own API (port 8001) and web app (port 3001) against `revlane_e2e`. They never touch the data `make dev` is using.
- The evaluation suite prints one line per layer with its result and its pass bar. Two questions in the chat set are deliberately beyond the stub.
- Tests never call a provider or a model. The live model clients are tested against a stand-in for the SDK.

## Signing in and roles

Everything but the health check needs a session. Sign in at `/login`; the session is a signed, HttpOnly cookie that lasts `SESSION_HOURS` (12 by default).

| Role | May |
| --- | --- |
| Viewer | See everything in their tenant and ask questions |
| Approver | Also approve and reject proposals, upload files, run the audit, move findings on |
| Admin | Also edit fee contracts, rules and agent settings, store sandbox keys, erase a shopper, export the audit log |

- Add a person with `make user ARGS="add someone@example.com 'Their Name' approver"`. The password is asked for at the prompt and must be at least ten characters.
- Change a password with `make user ARGS="password someone@example.com"`.
- Removing a person's row signs them out at once; changing their role applies from their next request.
- The console and the API must be on the same host name (`localhost` for both, or two subdomains of one site), or the browser will not send the cookie.
- `SESSION_SECRET` signs the cookies. Changing it signs everyone out.
- Single sign-on and a second factor are stubs behind the interfaces in `apps/api/revlane/auth/providers.py`.
- Metrics have their own bearer token, `METRICS_TOKEN`, and are switched off while it is empty.

## Using a real model

Set `LLM_API_KEY` and `LLM_STUB=false` in `.env`. `LLM_MODEL` defaults to `claude-sonnet-5-5`. The synthetic merchant is safe to send; before any real merchant data reaches a model, the key needs zero-retention terms.

## Adding a connector

1. Write `apps/api/revlane/connectors/<provider>.py`: a class extending `BaseConnector` with `provider`, `classify` (which kind of record a payload is) and `map` (one raw record to canonical records). Scrub every payload with `sanitize.scrub` before it is returned.
2. Register it in `connectors/registry.py` (`PROVIDERS`, `connector_for`) and add the provider to `PROVIDERS` in `db/models.py`. The provider column has a check constraint, so add a migration.
3. Add its status words to `connectors/statuses.py`. Check each against the provider's own documentation, and pin them in `tests/test_status_contract.py`: that test fails if a word is added or removed without it.
4. Add mapper tests to `tests/test_mappers.py`, using payloads in the provider's real shape.
5. If the gateway settles on a cycle other than the next day, set it in the tenant's policy under `matching.cycle_days`.
6. Run `make lint test`. The isolation, privacy and network tests cover the new connector without changes.

A connector reads. It has no write action, and `tests/test_rules.py` fails if one is added.

## Adding a metric to Ask Revlane

1. Add it to `apps/api/revlane/chat/semantic/v1.yaml`: a description, the source it reads, and either a sum of a column or a ratio of two parts, each with its conditions. Nothing in that file is SQL.
2. If it needs a column the source's view does not have, add the column in a new migration that replaces the view (see `migrations/versions/20261116_0006_chat_views.py`), and list any new dimension under the source.
3. Give the stub the words people use for it: `METRIC_PHRASES` and `METRIC_WORDS` in `chat/models.py`.
4. Add at least one question to `apps/api/evals/chat_questions.py` with its SQL ground truth written against the tables, not the view.
5. Run `make test eval`.

## Adding a playbook

1. Add `apps/api/revlane/harness/playbooks/<name>.yaml`: the tools it may read with, the actions it may propose (each `approval: required`), limits, and when to escalate. Loading it fails if it names a tool or action the harness does not have.
2. If it needs a new tool, add it to `harness/tools.py` and its `REGISTRY`. A tool only reads. Wrap any text that came from a file or a provider with `untrusted(...)`.
3. If it needs a new action, add the type to `ACTION_TYPES` in `harness/playbook.py` and a drafter to `DRAFTERS` in `harness/actions.py`. An action only drafts a document; the database refuses it without an approval.
4. Give the stub a script for it in `llm/stub.py`, so tests and the evaluation run without a key.
5. Add tests on the synthetic merchant, and a case to `evals/test_layers.py` if it makes a diagnosis.

## Data lifecycle

- **Audit log.** `GET /api/v1/audit-log/export` downloads the whole log as CSV. It holds hashes of payloads, never the payloads.
- **Retention.** Set `{"retention": {"days": 365}}` in a tenant's policy, then schedule `make retention` daily. It deletes provider records older than the period and keeps findings, proposals, approvals, claims and the audit log.
- **Erasure.** `POST /api/v1/privacy/erasures` with a shopper's `phone` or `email` (or their `customer_token`) removes the token that links their payments. The contact is used to work out the token and is not kept.
- **Offboarding.** `make offboard ARGS=<tenant id>` deletes the tenant's credentials from the vault at once, marks its accounts revoked, stops queued runs and switches its agents off.

## Watching it run

- **Traces.** Every agent run records each model turn and tool call with its duration: `GET /api/v1/agents/runs/<id>/trace`. The same steps are logged as JSON lines by the `revlane.trace` logger, with the run's id as `trace_id`.
- **Metrics.** `GET /api/v1/metrics` with `Authorization: Bearer <METRICS_TOKEN>` is in the Prometheus text format: sync lag per provider, unmapped statuses, findings and their amounts, agent runs, model calls and tokens.
- **Kill switches.** `AGENTS_ENABLED=false` stops every agent; `{"agents": {"enabled": false}}` in a tenant's policy stops that tenant's; the settings endpoint stops one agent.

## When something goes wrong

| What you see | What to do |
| --- | --- |
| "Revlane can't reach its API" in the console | Start it with `make dev`; check port 8000 is free |
| `The role revlane_chat does not exist` on migrate | Run `make db` |
| `TOKEN_HASH_ROOT_KEY is not set` | `cp .env.example .env` |
| "That email and password do not match an account" for the demo user | `DEMO_USER_PASSWORD` was empty or different when the user was created: `make user ARGS="password priya.menon@acmetravel.example"` |
| "Sign-in is not set up" | `SESSION_SECRET` is empty in `.env` |
| The console keeps returning to the sign-in page | The console and API are on different host names, so the cookie is not sent |
| A run stays "queued" | No worker is running: `make worker`, or use `make dev` |
| A run ends "escalated" | Read its `error`: a missing contract, low confidence, or the run used its whole budget |
| An agent says "Not run yet" | Run the audit from the chat page or the agent's page |
| A provider status is unknown | Connector health and the metrics show it; add the word to the mapping with a contract test |
