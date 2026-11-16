import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { expect, test, type Locator, type Page } from "@playwright/test";

// The console on an audited synthetic merchant: walk every view and every
// suggestion chip, approve and undo a fix, save a routine and a skill, and
// connect the sample MCP server. Reconciliation and Fee audit show live data;
// these tests only read it, and take their decisions on the preview agents.
// Navigation is by clicking, because console state lives in memory per session.

const nav = (page: Page): Locator => page.getByRole("navigation", { name: "Main" });
const title = (page: Page): Locator => page.getByTestId("view-title");
const lastReply = (page: Page): Locator => page.getByTestId("bot-message").last();

async function open(page: Page): Promise<void> {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Good morning, Priya" })).toBeVisible();
}

async function go(page: Page, name: string): Promise<void> {
  await nav(page).getByRole("link", { name: new RegExp(`^${name}`) }).click();
}

test.beforeEach(async ({ page }) => {
  await open(page);
});

test("walks all five views", async ({ page }) => {
  await expect(title(page)).toHaveText("New chat");
  await expect(page.getByText("Sample data")).toBeVisible();
  await expect(nav(page).getByText("Priya Menon")).toBeVisible();
  await expect(nav(page).getByText("Acme Travel · Admin")).toBeVisible();
  await expect(nav(page).getByRole("button", { name: "Sign out" })).toBeVisible();

  await go(page, "Agents");
  await expect(title(page)).toHaveText("Agents");
  await expect(page.getByRole("heading", { name: "Agents", level: 1 })).toBeVisible();
  await expect(
    page.getByText("Six agents, each guarding one way money slips away."),
  ).toBeVisible();
  await expect(page.getByTestId(/^agent-tile-/)).toHaveCount(6);

  await go(page, "Workflows");
  await expect(page.getByRole("heading", { name: "Workflows", level: 1 })).toBeVisible();
  await expect(page.getByText("High-value booking rescue")).toBeVisible();
  await expect(page.getByText("Daily close")).toBeVisible();

  await go(page, "Approvals");
  await expect(
    page.getByRole("heading", { name: "4 fixes are waiting for you" }),
  ).toBeVisible();
  await expect(page.getByTestId(/^approval-/)).toHaveCount(4);
  await expect(page.getByTestId(/^decided-/)).toHaveCount(2);

  await go(page, "Connections");
  await expect(page.getByRole("heading", { name: "Connections", level: 1 })).toBeVisible();
  // The gateways, the bank and the orders are real connections with records behind them.
  await expect(page.getByTestId("connection-bank")).toContainText("Connected");
  await expect(page.getByTestId("connection-bank")).toContainText("bank credits");
  await expect(page.getByTestId("connection-razorpay")).toContainText("payments");
  await expect(page.getByTestId("connection-razorpay").getByText("Sandbox keys")).toBeVisible();
  // The rest are still previews.
  await expect(page.getByRole("listitem").filter({ hasText: "Zoho Books" })).toContainText(
    "Preview",
  );
  await expect(page.getByRole("heading", { name: "Bring any system" })).toContainText("Preview");

  await go(page, "Chat");
  await expect(page.getByRole("heading", { name: "Good morning, Priya" })).toBeVisible();
});

type Chip = { chip: string; question: string; shows: string; action?: string; live?: boolean };
const chips: Chip[] = [
  {
    chip: "Where did we lose the most?",
    question: "Where did we lose the most last week?",
    shows: "Money lost to leaks by audit, in the last seven days",
    action: "Review fixes",
    live: true,
  },
  {
    chip: "Why did success drop?",
    question: "Why did success drop on Tuesday?",
    shows: "Payment success by hour, Tuesday",
    action: "Show me the recovery plan",
  },
  {
    chip: "Audit our fees",
    question: "Which gateway is cheapest for cards?",
    shows: "The effective fee rate for card by provider",
    action: "Open Fee audit",
    live: true,
  },
  {
    chip: "Disputes due",
    question: "Which disputes should we contest?",
    shows: "DP-2291",
    action: "Show me the responses",
  },
  {
    chip: "Failed auto-debits",
    question: "Why are subscription debits failing?",
    shows: "Retry after 5th",
    action: "Show me the plan",
  },
  {
    chip: "Build a workflow",
    question: "Build a workflow for failed bookings over ₹40K",
    shows: "When a booking payment of ₹40,000+ fails",
  },
];

test("every suggestion chip gets its answer", async ({ page }) => {
  for (const item of chips) {
    await page.getByRole("button", { name: item.chip, exact: true }).click();

    await expect(page.getByTestId("user-message")).toHaveText(item.question);
    await expect(page.getByRole("status").filter({ hasText: "…" })).toBeVisible();
    await expect(lastReply(page).getByText(item.shows)).toBeVisible();
    await expect(title(page)).toHaveText("Revenue chat");
    if (item.action) {
      await expect(lastReply(page).getByRole("button", { name: item.action })).toBeVisible();
    }
    // Answers from the real backend carry no pill; sample replies say they are previews.
    await expect(lastReply(page).getByText("Preview", { exact: true })).toHaveCount(
      item.live ? 0 : 1,
    );

    await page.getByRole("button", { name: "New chat" }).click();
    await expect(page.getByRole("heading", { name: "Good morning, Priya" })).toBeVisible();
  }
});

test("the weekly summary and the leak answer show the engines' numbers", async ({ page }) => {
  // Two real agents and four previews add up to the prototype's headline.
  await expect(page.getByText("₹22.6L slipped away · 4 fixes waiting")).toBeVisible();
  await page.getByRole("button", { name: /Weekly summary/ }).click();

  const reply = lastReply(page);
  await expect(reply.getByRole("row", { name: /reconciliation/ })).toContainText("₹3.1L");
  await expect(reply.getByRole("row", { name: /fee_audit/ })).toContainText("₹2.6L");
  await expect(reply.getByTestId("live-bars").getByRole("listitem")).toHaveCount(2);
  await expect(reply.getByText("Overall it is ₹5.7L.")).toBeVisible();
  await expect(reply.getByText(/^Source: .+:q1$/)).toBeVisible();

  await reply.getByText("Used 1 tool").click();
  await expect(reply.getByText("query(lost_minor)")).toBeVisible();
});

test("a question with no metric behind it is refused by name", async ({ page }) => {
  await page.getByLabel("Message Revlane").fill("What is our customer churn?");
  await page.getByRole("button", { name: "Send" }).click();

  await expect(
    lastReply(page).getByText("I can't answer that: no metric is defined for customer churn."),
  ).toBeVisible();
  await expect(lastReply(page).getByRole("table")).toHaveCount(0);
});

test("approves and undoes a fix, in chat and in Approvals", async ({ page }) => {
  await page.getByRole("button", { name: "Where did we lose the most?" }).click();
  await lastReply(page).getByRole("button", { name: "Review fixes" }).click();

  // Two real agents and two previews are waiting. The decisions here are on the previews.
  const fix = page.getByTestId("fix-failure");
  await expect(page.getByTestId(/^fix-(?!footer)/)).toHaveCount(4);
  await expect(page.getByTestId("fix-recon").getByText("₹3.1L at stake")).toBeVisible();
  await expect(page.getByTestId("fix-recon").getByText("Needs you")).toBeVisible();
  await expect(fix.getByText("Needs you")).toBeVisible();
  await expect(fix.getByText("₹9.4L at stake")).toBeVisible();

  await fix.getByRole("button", { name: "Approve" }).click();
  await expect(fix.getByText("Running")).toBeVisible();
  await expect(page.getByTestId("fix-footer")).toHaveText(
    "1 of 4 decided · also in Approvals",
  );
  await expect(nav(page).getByRole("link", { name: /^Approvals,? 3 waiting$/ })).toBeVisible();

  await fix.getByRole("button", { name: "Undo" }).click();
  await expect(fix.getByText("Needs you")).toBeVisible();
  await expect(page.getByTestId("fix-footer")).toHaveText(
    "0 of 4 decided · also in Approvals",
  );

  await go(page, "Approvals");
  await expect(page.getByTestId("approval-failure")).toContainText("Preview");
  await expect(page.getByTestId("approval-recon")).not.toContainText("Preview");
  await page.getByRole("button", { name: "Approve Failed-payment recovery" }).click();
  await expect(
    page.getByRole("heading", { name: "3 fixes are waiting for you" }),
  ).toBeVisible();
  await expect(page.getByTestId("decided-failure")).toContainText("Running");

  await page.getByRole("button", { name: "Reject Routing advisor" }).click();
  await expect(page.getByTestId("decided-routing")).toContainText("Rejected");

  // Undo from the agent's own page puts it back in the queue.
  await go(page, "Agents");
  await page.getByTestId("agent-tile-failure").click();
  await expect(title(page)).toHaveText("Agents / Failed-payment recovery");
  await page.getByRole("button", { name: "Undo" }).click();
  await expect(page.getByRole("button", { name: "Approve" })).toBeVisible();
  await expect(nav(page).getByRole("link", { name: /^Approvals,? 3 waiting$/ })).toBeVisible();
});

test("opens a real agent, simulates its rules and sees its autonomy limits", async ({ page }) => {
  await go(page, "Agents");
  await expect(page.getByTestId("agent-tile-recon")).not.toContainText("Preview");
  await expect(page.getByTestId("agent-tile-failure")).toContainText("Preview");
  await page.getByTestId("agent-tile-recon").click();

  await expect(page.getByRole("heading", { name: "Reconciliation", level: 1 })).toBeVisible();
  await expect(page.getByText("₹3.1L was paid but has not reached the bank")).toBeVisible();
  await expect(page.getByText("findings_for → evidence_for → bank_credits")).toBeVisible();
  await expect(page.getByTestId("finding")).toHaveCount(3);
  await expect(page.getByText("Preview", { exact: true })).toHaveCount(0);

  // Observe and Suggest are built; Batch and Auto are shown and cannot be chosen.
  const levels = page.getByRole("group", { name: "Autonomy level" });
  await expect(levels.getByRole("button", { name: "Suggest" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  for (const name of ["Batch", "Auto"]) {
    await expect(levels.getByRole("button", { name })).toBeDisabled();
    await expect(levels.getByRole("button", { name })).toHaveAttribute(
      "title",
      "Not available yet: agents earn this level.",
    );
  }
  await expect(levels.getByRole("button", { name: "Observe" })).toBeEnabled();

  // The rules are the engine's own thresholds; simulating replays it and saves nothing.
  await page.getByRole("button", { name: "Edit rules" }).click();
  const grace = page.getByLabel("Flag unsettled after (working days past the cycle)");
  await expect(grace).toHaveValue("2");
  await grace.fill("15");
  await page.getByRole("button", { name: "Simulate on 90 days" }).click();
  await expect(page.getByTestId("rules-note")).toContainText(
    "With these rules it would find 2 findings worth ₹2.5L over 43 records",
  );
  await expect(page.getByTestId("rules-note")).toContainText(
    "With the rules in force it finds 3 findings worth ₹3.1L",
  );
  await grace.fill("two");
  await page.getByRole("button", { name: "Simulate on 90 days" }).click();
  await expect(page.getByTestId("rules-note")).toHaveText("Each rule is a whole number.");
  await page.getByRole("button", { name: "Done" }).click();
  await expect(grace).toHaveCount(0);

  await page.getByRole("button", { name: "Discuss in chat" }).click();
  const card = page.getByTestId("agent-card-recon");
  await expect(card.getByText("Why it happened")).toBeVisible();
  await expect(card.getByText("Payouts lower than the sum of their lines")).toBeVisible();
  await card.getByRole("link", { name: "Open agent ↗" }).click();
  await expect(title(page)).toHaveText("Agents / Reconciliation");
});

test("a preview agent keeps its sample rules and autonomy", async ({ page }) => {
  await go(page, "Agents");
  await page.getByTestId("agent-tile-failure").click();
  await expect(page.getByText("Preview", { exact: true }).first()).toBeVisible();

  const levels = page.getByRole("group", { name: "Autonomy level" });
  await levels.getByRole("button", { name: "Observe" }).click();
  await expect(page.getByTestId("level-note")).toHaveText(
    "Observe: it diagnoses and reports, and proposes nothing.",
  );

  await page.getByRole("button", { name: "Discuss in chat" }).click();
  const card = page.getByTestId("agent-card-failure");
  await card.getByRole("button", { name: "Edit rules" }).click();
  await expect(card.getByLabel("High-value threshold")).toHaveValue("₹25,000");
  await card.getByRole("button", { name: "Simulate on 90 days" }).click();
  await expect(card.getByText("41 actions")).toBeVisible();
  await card.getByRole("button", { name: "Done" }).click();
  await expect(card.getByLabel("High-value threshold")).toHaveCount(0);
});

test("saves a routine from the + menu and manages it", async ({ page }) => {
  await page.getByRole("button", { name: "Add files, skills or routines" }).click();
  await page.getByRole("button", { name: /Create a routine/ }).click();

  const input = page.getByLabel("Message Revlane");
  await expect(input).toHaveValue(/^Every Monday at 9 am/);
  await input.press("Enter");

  const reply = lastReply(page);
  await expect(reply.getByText("Mondays 9:00 · all gateways")).toBeVisible();
  await reply.getByRole("button", { name: "Save routine" }).click();
  await expect(reply.getByText("Saved · first run Monday")).toBeVisible();

  await page.getByRole("button", { name: "Routines", exact: true }).click();
  const list = page.getByRole("list", { name: "Routines" });
  await expect(list.getByRole("listitem")).toHaveCount(4);
  await list.getByRole("button", { name: "Pause Daily close" }).click();
  await expect(list.getByRole("listitem").filter({ hasText: "Daily close" })).toContainText(
    "Paused",
  );
  await list.getByRole("button", { name: "Run Fee claims now" }).click();
  await expect(list.getByRole("listitem").filter({ hasText: "Fee claims" })).toContainText(
    "Ran just now",
  );

  await go(page, "Workflows");
  const scheduled = page.getByRole("region", { name: "On a schedule (routines)" });
  await expect(scheduled.getByText("Weekly leakage report")).toBeVisible();
  await expect(page.getByRole("button", { name: "Resume Daily close" })).toBeVisible();
});

test("saves a skill and then offers it in the + menu", async ({ page }) => {
  const plus = page.getByRole("button", { name: "Add files, skills or routines" });
  await plus.click();
  await page.getByRole("button", { name: "Use a skill" }).click();
  await expect(page.getByRole("button", { name: "Refund policy v3" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Month-end close" })).toHaveCount(0);

  await page.getByRole("button", { name: /Create a skill/ }).click();
  const reply = lastReply(page);
  await expect(reply.getByLabel("Name")).toHaveValue("Month-end close");
  await reply.getByRole("button", { name: "Save skill" }).click();
  await expect(reply.getByText(/^Saved\. It's now in \+ → Use a skill/)).toBeVisible();

  await plus.click();
  await page.getByRole("button", { name: "Use a skill" }).click();
  await page.getByRole("button", { name: "Month-end close" }).click();
  await expect(page.getByTestId("attachments")).toContainText("Month-end close");

  await page.getByLabel("Message Revlane").fill("Audit our fees");
  await page.getByRole("button", { name: "Send" }).click();
  await lastReply(page).getByText("Used 2 tools").click();
  await expect(lastReply(page).getByText("skill: Month-end close")).toBeVisible();
  await expect(lastReply(page).getByText("query(fee_overcharge_minor)")).toBeVisible();
});

test("uploads a statement from chat and is told what was read", async ({ page }) => {
  // A real file: one credit that matches nothing, so the findings stay as they are.
  const name = `statement_${Date.now()}.csv`;
  const file = join(mkdtempSync(join(tmpdir(), "revlane-chat-")), name);
  writeFileSync(
    file,
    "Date,Narration,Chq./Ref.No.,Value Dt,Withdrawal Amt.,Deposit Amt.,Closing Balance\n" +
      `01/11/26,NEFT CR-SBIN0000691-SOMEONE-${Date.now()},UTR${Date.now()},01/11/26,,4321.00,10.00\n`,
  );
  const plus = page.getByRole("button", { name: "Add files, skills or routines" });
  await plus.click();
  await expect(page.getByRole("button", { name: /Upload a file/ })).toBeVisible();
  await page.getByTestId("chat-file").setInputFiles(file);

  const input = page.getByLabel("Message Revlane");
  await expect(page.getByTestId("attachments")).toContainText(name);
  await expect(input).toHaveAttribute("placeholder", /^Ask about this file/);

  await plus.click();
  await page.getByRole("button", { name: /Add from connections/ }).click();
  await page.getByRole("button", { name: "Remove Shopify · October bookings" }).click();
  await expect(page.getByTestId("attachments")).not.toContainText("Shopify");

  await input.fill("What is in here?");
  await input.press("Enter");
  await expect(page.getByTestId("user-message")).toContainText(name);
  await expect(
    lastReply(page).getByText(`Read 1 records from ${name} as a bank file: 1 new.`),
  ).toBeVisible();
  await expect(lastReply(page).getByRole("button", { name: "Run the audit" })).toBeVisible();
});

test("builds a workflow, simulates it and requests approval", async ({ page }) => {
  await go(page, "Workflows");
  await page.getByRole("button", { name: "Build in chat" }).click();

  const draft = page.getByRole("article", { name: /workflow draft/ });
  await expect(draft.getByRole("listitem")).toHaveCount(6);
  await expect(draft.getByText("Draft", { exact: true })).toBeVisible();

  await draft.getByRole("button", { name: "Simulate on 90 days" }).click();
  await expect(draft.getByText("38 bookings worth ₹14.6L")).toBeVisible();
  await draft.getByRole("button", { name: "Request approval" }).click();
  await expect(draft.getByText("Waiting for 2 approvers")).toBeVisible();
  await expect(draft.getByText(/Sent to Priya \(Finance\) and Arjun \(Payments\)/)).toBeVisible();

  await draft.getByRole("link", { name: "Open in Workflows ↗" }).click();
  await expect(page.getByText("Waiting for 2 approvers")).toBeVisible();
});

test("connects the sample MCP server", async ({ page }) => {
  await go(page, "Connections");

  await expect(page.getByLabel("MCP server URL")).toHaveValue(
    "https://mcp.acme-travel.internal/inventory",
  );
  await expect(page.getByTestId("mcp-tools")).toHaveCount(0);
  await page.getByRole("button", { name: "Connect" }).click();

  const tools = page.getByTestId("mcp-tools");
  await expect(tools.getByText("acme-inventory · 4 tools found")).toBeVisible();
  await expect(tools.getByRole("listitem")).toHaveCount(4);
  await expect(tools.getByRole("listitem").filter({ hasText: "cancel_booking" })).toContainText(
    "Blocked",
  );
});

test("notifications, recents, Escape and the sidebar pin", async ({ page }) => {
  const bell = page.getByRole("button", { name: /^Notifications/ });
  await expect(bell).toHaveAccessibleName("Notifications, unread");
  await bell.click();
  const panel = page.getByRole("region", { name: "Notifications" });
  // Built from what happened to findings and proposals, not from sample data.
  await expect(panel.getByRole("button", { name: /A fix is waiting for you/ }).first()).toBeVisible();
  await expect(panel.getByText("5 disputes due in 48 hours")).toHaveCount(0);

  await page.keyboard.press("Escape");
  await expect(panel).toHaveCount(0);

  await page.getByRole("button", { name: "Add files, skills or routines" }).click();
  await expect(page.getByRole("group", { name: "Add to your message" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("group", { name: "Add to your message" })).toHaveCount(0);

  // A notification about a finding opens the agent that found it.
  await bell.click();
  await panel.getByRole("button", { name: /New finding: ₹1.6L/ }).click();
  await expect(title(page)).toHaveText("Agents / Fee audit");
  await expect(bell).toHaveAccessibleName("Notifications");

  // A recent question starts a new chat; the first message folds the sidebar,
  // and pinning it brings the labels back.
  await nav(page).getByRole("button", { name: "Card fees by gateway" }).click();
  await expect(page.getByTestId("user-message")).toHaveCount(1);
  await expect(page.getByTestId("user-message")).toHaveText(
    "Which gateway is cheapest for cards?",
  );
  await expect(lastReply(page).getByText(/is lowest at \d+\.\d\d%/)).toBeVisible();
  await expect(nav(page).getByText("Recents")).toHaveCount(0);
  await page.getByRole("button", { name: "Keep sidebar open" }).click();
  await expect(nav(page).getByText("Recents")).toBeVisible();
});
