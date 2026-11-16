# Revlane: handover package for Claude Code

Everything Claude Code needs to build Revlane from the prototype and the design documents.

## How to use it

1. Create an empty git repository and copy the contents of this folder into its root.
2. Fill in `docs/decisions-needed.md` (ten minutes; the build can start with the defaults marked there).
3. Copy `.env.example` to `.env` and add a model API key if you have one. The build works without it (see "stub mode").
4. Open the repository in Claude Code and paste the contents of `KICKOFF_PROMPT.md`.

Claude Code reads `CLAUDE.md` automatically at the start of every session. The other documents are loaded on demand, when a milestone needs them.

## What is in here

| File | Purpose |
| --- | --- |
| `CLAUDE.md` | Standing instructions: what Revlane is, stack, commands, rules that must never be broken |
| `KICKOFF_PROMPT.md` | The first message to paste into Claude Code |
| `docs/build-plan.md` | Nine milestones, each with scope and acceptance tests |
| `docs/technical-design.md` | Architecture, data model, connectors, matching and fee logic, harness, chat, safety, security |
| `docs/design-spec.md` | Screens, components, design tokens, copy and sample data, taken from the prototype |
| `docs/synthetic-merchant.md` | The fictional merchant, the planted leaks and the answer key the engines are tested against |
| `docs/product-brief.md` | Why Revlane exists and what the first version must prove |
| `docs/decisions-needed.md` | Choices only you can make, with a default for each |
| `design/prototype.dc.html` | The prototype's source: markup, styles, copy and sample data |
| `.env.example` | Environment variables |

## The one scoping decision already made

The prototype shows the whole product: six agents, workflows, routines, skills, bring-your-own MCP. The technical design builds only part of that in the first version: the audit, reconciliation, fee audit, chat and approvals.

This package reconciles the two as follows:

- **The whole console is built as designed.**
- **Real engines sit behind** Reconciliation, Fee audit, Ask Revlane, Approvals and the gateway, bank and order connections.
- **Everything else runs on the prototype's sample data** and carries a "Preview" tag: failed-payment recovery, routing advisor, chargeback, subscription recovery, workflows, routines, skills and custom MCP.

If you want a different split, change the "Real or preview" table in `docs/design-spec.md` before starting; the build plan follows that table.
