# Decisions needed

Fill in the "Answer" column. Where it is left blank, Claude Code uses the default.

| # | Decision | Default | Answer |
| --- | --- | --- | --- |
| 1 | Which two gateways get live sandbox connectors first? | Razorpay and PayU. Cashfree is file-only. | |
| 2 | Do you have sandbox credentials for those gateways? | No. Build and test against synthetic provider-shaped files; add sandbox calls behind a flag. | |
| 3 | Which model provider and model? | Anthropic API, a mid-tier model, set by `LLM_MODEL`. Stub mode when no key is set. | |
| 4 | Sign-in for the first version | Email and password with one seeded demo tenant. SSO and MFA are stubbed behind an interface. | |
| 5 | Cloud and region for deployment | Not deployed in this build. Infrastructure code targets AWS Mumbai as a stub in the last milestone. | |
| 6 | Demo merchant shown in the console | "Acme Travel", user "Priya Menon, Finance", as in the prototype. | |
| 7 | How claims leave Revlane | Download as PDF and copy as email text. Nothing is sent. | |
| 8 | Fonts | Inter, Newsreader and JetBrains Mono, self-hosted, as in the prototype. | |
| 9 | Preview screens: keep or hide? | Keep, tagged "Preview", running on sample data. | |
| 10 | Licence for the repository | Private, no licence file. | |

## Things you must supply before going beyond synthetic data

- Sandbox API keys for each gateway (never production keys).
- Two or three real settlement files and bank statements, under NDA, with customer details removed.
- A model API key with zero-retention terms if any real merchant data will reach a model.
