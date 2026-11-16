# Design spec

Taken from `design/prototype.dc.html` ("Revlane Prototype", 1440 × 1000, clickable). The prototype is the source of truth for layout, styling and copy. Port it; do not redesign it.

The prototype file is a template: markup with `{{bindings}}`, `<sc-if>` and `<sc-for>` tags, one `<style>` block, and one script holding state and sample data. It does not run on its own. Read it for exact markup, inline styles, SVG icon paths and strings.

## Layout

- **Shell:** a left sidebar and a main column, full viewport height, page background `#FAF9F5`.
- **Sidebar:** 264px when pinned, 68px when collapsed. Collapsed, it expands on hover as an overlay with a shadow. Background `#F4F2EC`. Contents, top to bottom: logo and wordmark, pin toggle, "New chat" button with a terracotta circle, five navigation items, "Recents" (five saved questions, shown only when expanded), then the user block at the bottom (avatar initial, "Priya Menon", "Acme Travel · Finance").
- **Navigation:** Chat, Agents, Workflows, Approvals, Connections. The active item has background `#E9E5DB`. Approvals shows a count badge when proposals are pending.
- **Header:** the view title on the left; on the right a "Sample data" pill and a notifications bell that opens a 380px panel.
- **Chat column:** centred, maximum width 740px.

## Design tokens

| Token | Value |
| --- | --- |
| Page background | `#FAF9F5` |
| Sidebar and soft surfaces | `#F4F2EC` |
| Card | `#FFFFFF`, border `#EAE7DE`, radius 16px, padding 20px, shadow `0 1px 2px rgba(31,30,29,.03)` |
| Text | `#1F1E1D`; secondary `#3D3C38`, `#4A4944`; muted `#6B6A63`; placeholder `#8F8D85` |
| Accent (terracotta) | `#D97757`, hover `#C96A4B`; link `#A84A2B`, hover `#7A3319` |
| Positive pill | background `#E3EDE5`, text `#23513A` |
| Neutral pill | background `#EFECE4`, text `#3D3C38` |
| Warm pill | background `#F5E2D7`, text `#7A3319` |
| Tool tag | background `#E7E9F2`, text `#2E3A6B` |
| Dark tag (needs approval, skill) | background `#1F1E1D`, text `#FAF9F5` |
| Borders | `#EAE7DE`, `#E3E0D6`, `#DEDAD0`, `#E6E3D9`; row divider `#F1EFE8` |
| Chart colours, one per leak | `#C4643F`, `#E39572`, `#8C3A1C`, `#BC8B66`, `#7A6A5C`, `#D9B8A3` |
| Radius | 10px buttons and nav items, 12px chips and soft blocks, 16px cards and menus, 22px composer, 999px pills |
| Fonts | Inter for UI; Newsreader (class `serif`, weight 400, letter-spacing −0.012em) for headings; JetBrains Mono for tool names and query ids |
| Sizes | 46px greeting; 16px composer input; 15px chat text; 14px body and nav; 13.5px buttons; 12px pills; 11.5px eyebrow (uppercase, letter-spacing 0.07em) |
| Numbers | `font-variant-numeric: tabular-nums` on every amount |
| Motion | `rise` entrance animation 0.35s ease-out on new messages and panels; 0.15s transitions on hover |

Component classes in the prototype's `<style>` block map one-to-one to components: `card`, `soft`, `btn`, `btnp` (primary, dark), `btnc` (accent), `chip`, `side`, `icon`, `composer`, `send`, `menu`, `mi`, `track`, `pill`, `line`, `att`, `fld`.

## Screens

### 1. Chat (home)

- **Empty state:** a "Weekly summary" pill ("₹22.6L slipped away · 4 fixes waiting" with an arrow), the greeting "Good morning, Priya" in the serif face with a small spinning star, the composer, and six suggestion chips.
- **Composer:** rounded 22px box, placeholder "Message Revlane", a "+" menu on the left, a "Revlane · Acme data" label, and a terracotta send button. Attached items appear as small chips above the input with a remove button.
- **"+" menu:** Upload a file (statements, settlement reports, contracts); Add from connections (Shopify, Zoho Books, BigQuery, your MCP); Use a skill (opens a submenu of skills); Create a skill; Create a routine; Manage routines.
- **User message:** right-aligned bubble, background `#F0EDE5`, radius 18px, with any attached files above it.
- **Assistant message:** a terracotta star on the left, then, in order: a collapsed "steps" line listing tool names in mono; the answer text; then one of the rich blocks below; then a source line in muted mono text; then an action button.
- **Thinking state:** a line such as "Reading 41,280 attempts across 3 gateways…" for about a second.

Rich blocks inside an assistant message:

| Block | Contents |
| --- | --- |
| Table | A three-column table with a muted header row |
| Leak chart | Title and total, one horizontal bar per leak with label and amount, and a split by gateway |
| Hourly chart | "Payment success by hour, Tuesday" with the 2–4 pm dip marked |
| Fix list | One row per pending proposal: name, amount at stake, status pill, proposal text, Approve, Details and Reject buttons; after a decision, the result text and Undo |
| Agent card | Family and name, amount, status pill, "Why it happened" with cause bars and percentages, "Proposed fix" with a four-item fact grid, Approve, Edit rules, Reject, "Open agent ↗"; an inline rules editor with "Simulate on 90 days" |
| Workflow draft | Name, status pill, six numbered steps each with a tag (Trigger, Your tool, Needs approval, Revlane), "Simulate on 90 days", "Request approval", "Open in Workflows ↗" |
| Routine card | Name, schedule, "Save routine", then a saved state |
| Routine list | Each routine with schedule, status pill, "Run now" and Pause or Resume |
| Skill draft | Name field, instructions field, "Applies to" checkboxes (Reconciliation, Fee audit, Chat), "Save skill" |

### 2. Agents

- **Grid:** heading "Agents", subheading "Six agents, each guarding one way money slips away." Six cards: family eyebrow, status pill, name, one-line headline, "Found last 7 days" and the amount.
- **Agent detail:** back link, "Discuss in chat", family and name, headline, "Lost last 7 days" amount and status pill; "Why it happened" with cause bars; the tool chain in mono; "Proposed fix" with a fact grid and Approve and Reject; the result text and Undo after a decision.
- **Autonomy control:** "How much it can do on its own", a four-segment control (Observe, Suggest, Batch, Auto), the note "Agents earn autonomy: 50+ proposals at 95% approval, then 30 days without incidents", and one line describing the selected level.

### 3. Workflows

Heading "Workflows", subheading "Event-driven workflows and scheduled routines, in one place. Build new ones from chat." A "Build in chat" button. Two lists: "Triggered by events" (name, description, status pill) and "On a schedule (routines)" (name, schedule, status pill, Pause or Resume).

### 4. Approvals

A title with the pending count, the line "Nothing that touches money or customers runs without you, until you give an agent more autonomy." A list of pending proposals (name, amount at stake, proposal text, Reject and Approve), then a "Decided" list with status pills.

### 5. Connections

Heading "Connections", the line "Revlane reads with your credentials and acts only through your own accounts. It never holds your money." A grid of connection cards: initial, name, status pill, kind, one-line detail. Below it, "Bring any system": an MCP server URL field and Connect button; after connecting, "acme-inventory · 4 tools found" with each tool and its permission.

## Real or preview

This table decides what gets a real backend. Everything marked Preview is served from the fixtures file and shows a "Preview" pill next to its title.

| Surface | First version | Notes |
| --- | --- | --- |
| Chat: questions about leaks, fees, settlements, uploaded statements | Real | Semantic layer and engines |
| Chat: success-rate drop, disputes, subscriptions, routing answers | Preview | Canned replies from fixtures |
| Chat: build a workflow, create a routine, create a skill | Preview | Canned replies from fixtures |
| Agents: Reconciliation, Fee audit | Real | Engines, harness, approvals |
| Agents: Failed-payment recovery, Routing advisor, Chargeback, Subscription recovery | Preview | Fixtures |
| Agent rules editor and "Simulate on 90 days" | Real for the two real agents | Simulation replays the engine with the edited thresholds |
| Autonomy control | Real, limited to Observe and Suggest | Batch and Auto are visible and disabled, with a tooltip |
| Approvals | Real | Preview agents' proposals appear with the Preview pill and change only local state |
| Workflows and routines | Preview | Fixtures |
| Connections: gateways, bank, orders | Real | Credential entry for two gateways; file upload for the rest |
| Connections: Zoho Books, Slack, WhatsApp, Salesforce, BigQuery, custom MCP | Preview | Fixtures |
| Notifications | Real for findings and approvals | Generated from finding and proposal events |
| "Sample data" header pill | Shown while the tenant is the synthetic merchant | Hidden for a tenant with real connections |

## Sample data

The script in the prototype holds the demo content. Move it, unchanged, into `apps/api/revlane/fixtures/acme_travel.json` and serve it from `/api/v1/fixtures`.

- **Merchant:** Acme Travel. **User:** Priya Menon, Finance. **Second approver:** Arjun, Payments.
- **Headline:** ₹22.6L slipped away last week across three gateways; ₹6.8L recovered this month; four fixes waiting.
- **Leaks, last 7 days (₹ lakh):** failed payments 9.4; weaker gateway for some banks 3.5; paid, never received 3.1; fee overcharges 2.6; failed auto-debits 2.2; disputes at risk 1.8. Total 22.6.
- **By gateway (₹ lakh):** Razorpay 11.8, PayU 7.4, Cashfree 3.4.
- **Each agent** has a headline, three causes with percentages, a proposal, four facts, a tool chain, a result text and four editable settings. Copy them exactly.
- **Initial states:** failure, routing, reconciliation and fees are pending; chargeback and subscriptions are approved. Autonomy levels: failure 1, routing 0, reconciliation 1, fees 1, chargeback 1, subscriptions 2.
- **Suggestion chips:** Where did we lose the most? · Why did success drop? · Audit our fees · Disputes due · Failed auto-debits · Build a workflow.
- **Skills:** Refund policy v3, Tone of voice, EMI rules, Dispute evidence checklist.
- **Routines:** Weekly leakage report; Daily close; Fee claims; Dispute deadline check.
- **Workflows:** High-value booking rescue (draft), Failed-payment rescue (live), Dispute desk (live).
- **Connections:** Razorpay, PayU, Cashfree, HDFC Bank, Shopify, Zoho Books, Slack, WhatsApp (connected); Salesforce, BigQuery (available).

**The real engines must reproduce the prototype's reconciliation and fee numbers** when run on the synthetic merchant. `docs/synthetic-merchant.md` is written so that they do: reconciliation finds ₹3.1L (40 auto-refunded bookings ₹1.3L, three short marketplace payouts ₹1.2L, 22 captured-not-settled payments ₹0.6L) and the fee audit finds ₹2.6L across 1,912 lines.

## Accessibility and behaviour

- Every icon button has an `aria-label`; the sidebar is a `nav` landmark.
- Keyboard: Enter sends a message; Escape closes menus and the notifications panel.
- Minimum supported width is 1280px. Below that, the sidebar collapses by default. No mobile layout in this build.
- Amounts under ₹1 lakh are shown in full with Indian digit grouping (₹46,200); from ₹1 lakh, in lakh with one decimal (₹2.6L); from ₹1 crore, in crore with one decimal (₹4.2 Cr).
