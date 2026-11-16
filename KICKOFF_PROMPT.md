# Kickoff prompt

Paste everything below the line into Claude Code, in the repository root.

---

You are building Revlane. `CLAUDE.md` has the standing rules. Before writing any code:

1. Read `docs/build-plan.md`, `docs/technical-design.md`, `docs/design-spec.md` and `docs/synthetic-merchant.md` in full. Skim `design/prototype.dc.html` so you know what the console looks like.
2. Read `docs/decisions-needed.md`. Where an answer is blank, use the default written beside it.
3. Tell me, in under 300 words: your understanding of what the first version must do, the split between real and preview screens, and anything in the documents that conflicts or is unclear. Wait for my reply.

After I reply, start Milestone 0 from `docs/build-plan.md`. For each milestone:

- Write the acceptance tests first.
- Build until `make lint test e2e eval` passes.
- Stop and give me a summary: what was built, what you skipped and why, decisions you made that the documents did not cover, and the exact commands I should run to see it working.
- Do not start the next milestone until I say so.

Constraints that override anything else: money is integer paise; the model never types a number or writes SQL; nothing is sent to providers or customers; every proposal needs an approval; only synthetic or sandbox data.
