import { mkdtempSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { expect, test } from "@playwright/test";

import { API_URL, dev } from "./backend";

// Milestone 7 acceptance, with no sample data involved: load the synthetic
// merchant, run the audit, open the Reconciliation agent, approve the proposal,
// download the claim, ingest a later bank file that contains the recovered
// money, and see the finding move to "recovered".

const PROVIDERS = ["orders", "razorpay", "payu", "cashfree", "marketplace", "bank"];
const HEADER =
  "Date,Narration,Chq./Ref.No.,Value Dt,Withdrawal Amt.,Deposit Amt.,Closing Balance\n";
let merchant = "";

/** Payments before the records that refer to them, as the loader does it. */
function inOrder(names: string[]): string[] {
  const first = (name: string) => (/payment|transaction/.test(name) ? 0 : 1);
  return [...names].sort((a, b) => first(a) - first(b) || a.localeCompare(b));
}

test.beforeAll(() => {
  dev("reset");
  merchant = dev("files", mkdtempSync(join(tmpdir(), "revlane-flow-"))).trim();
});

test("from an empty tenant to recovered money", async ({ page, request }) => {
  test.setTimeout(180_000);

  // Nothing is connected yet: the console offers the three first steps.
  await page.goto("/");
  const setup = page.getByTestId("setup");
  await expect(setup).toContainText("Connect a gateway or upload files");
  await expect(setup.getByRole("button", { name: "Run the audit" })).toBeDisabled();
  await page.goto("/agents/recon");
  await expect(page.getByText("No audit has run yet.")).toBeVisible();
  await expect(page.getByText("Not run yet")).toBeVisible();

  // Load the synthetic merchant through the upload endpoint the console uses...
  for (const provider of PROVIDERS) {
    for (const name of inOrder(readdirSync(join(merchant, provider)))) {
      if (provider === "bank" && name.endsWith("-11.csv")) continue; // left for the screen
      const response = await request.post(
        `${API_URL}/connectors/${provider}/uploads?filename=${name}`,
        { data: readFileSync(join(merchant, provider, name)) },
      );
      expect(response.ok(), `${provider}/${name}`).toBeTruthy();
    }
  }
  const contracts = JSON.parse(
    readFileSync(join(merchant, "contracts", "fee_contracts.json"), "utf-8"),
  ) as unknown[];
  for (const contract of contracts) {
    expect((await request.post(`${API_URL}/fee-contracts`, { data: contract })).ok()).toBeTruthy();
  }
  // ...and the last bank statement through the Connections screen itself.
  await page.goto("/connections");
  const bank = page.getByTestId("connection-bank");
  const latest = readdirSync(join(merchant, "bank")).find((name) => name.endsWith("-11.csv"));
  await bank.getByLabel("Upload a file").setInputFiles(join(merchant, "bank", latest ?? ""));
  await expect(bank.getByRole("status")).toContainText("as a bank file");
  await expect(bank.getByText("Connected", { exact: true })).toBeVisible();
  await expect(page.getByTestId("connection-razorpay")).toContainText("payments");

  // Run the audit from the first-run card.
  await page.goto("/");
  await expect(setup.getByText("Done")).toHaveCount(2);
  await setup.getByRole("button", { name: "Run the audit" }).click();
  await expect(setup).toBeHidden({ timeout: 90_000 });
  await expect(page.getByText("₹22.6L slipped away · 4 fixes waiting")).toBeVisible();

  // The question from the prototype is answered with the engines' own numbers.
  await page.getByRole("button", { name: "Where did we lose the most?" }).click();
  const answer = page.getByTestId("bot-message").last();
  await expect(answer.getByRole("table")).toContainText("reconciliation");
  await expect(answer.getByRole("row", { name: /reconciliation/ })).toContainText("₹3.1L");
  await expect(answer.getByRole("row", { name: /fee_audit/ })).toContainText("₹2.6L");
  await expect(answer.getByText("Preview")).toHaveCount(0);

  // Open the Reconciliation agent and approve what it proposes.
  await page.goto("/agents");
  await page.getByTestId("agent-tile-recon").click();
  await expect(page.getByRole("heading", { name: "Reconciliation" })).toBeVisible();
  await expect(page.getByText("₹3.1L was paid but has not reached the bank")).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Ask the marketplace about ₹1.2L not settled in full" }),
  ).toBeVisible();
  const short = page.getByTestId("finding").filter({ hasText: "the marketplace" });
  await expect(short).toContainText("Recoverable");
  await page.getByRole("button", { name: "Approve" }).click();
  await expect(short).toContainText("Claimed");

  // Download the claim that the approval drafted.
  const downloading = page.waitForEvent("download");
  await page.getByRole("link", { name: "Download claim (PDF)" }).first().click();
  const download = await downloading;
  expect(download.suggestedFilename()).toMatch(/^claim-.+\.pdf$/);
  const claim = readFileSync(await download.path());
  expect(claim.subarray(0, 8).toString()).toBe("%PDF-1.4");
  expect(claim.includes("Amount claimed: INR 1,20,000.00")).toBeTruthy();
  expect(claim.includes("Payouts lower than their lines")).toBeTruthy();

  // Later, the marketplace pays the shortfall. The bank statement that shows it is uploaded...
  const today = new Date().toLocaleDateString("en-GB", {
    timeZone: "Asia/Kolkata",
    day: "2-digit",
    month: "2-digit",
    year: "2-digit",
  });
  const later = join(mkdtempSync(join(tmpdir(), "revlane-later-")), "hdfc_statement_later.csv");
  writeFileSync(
    later,
    `${HEADER}${today},NEFT CR-HDFC0000240-TRIPBAZAAR MARKETPLACE-SHORTFALL,UTRLATER0001,${today},,120000.00,99999999.00\n`,
  );
  await page.goto("/connections");
  await bank.getByLabel("Upload a file").setInputFiles(later);
  await expect(bank.getByRole("status")).toContainText("1 new");

  // ...and the next audit sees the money has arrived.
  await page.goto("/agents/recon");
  await page.getByRole("button", { name: "Run the audit" }).click();
  await expect(short).toContainText("Recovered", { timeout: 90_000 });
  await expect(short).toContainText("₹1.2L");

  // None of this was sample data.
  await expect(page.getByText("Preview")).toHaveCount(0);
  const notes = page.getByRole("button", { name: /Notifications/ });
  await notes.click();
  await expect(page.getByRole("region", { name: "Notifications" })).toContainText(
    "Recovered: ₹1.2L",
  );
});
