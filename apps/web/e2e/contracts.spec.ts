import { expect, type Page, test } from "@playwright/test";

type Row = Record<string, unknown> & { id: string };

// The API is stood in for here, so the form is tested without a database.
// Its own tests are in apps/api/tests/test_fee_contracts_api.py.
async function fakeApi(page: Page, rows: Row[]) {
  let next = rows.length + 1;
  await page.route("**/api/v1/fee-contracts**", async (route) => {
    const request = route.request();
    const id = new URL(request.url()).pathname.split("/fee-contracts/")[1];
    if (request.method() === "GET") {
      return route.fulfill({ json: { items: rows, next_cursor: null } });
    }
    if (request.method() === "DELETE") {
      rows.splice(
        rows.findIndex((row) => row.id === id),
        1,
      );
      return route.fulfill({ status: 204 });
    }
    const body = request.postDataJSON() as Record<string, unknown>;
    if (body.rate_bps === 999) {
      return route.fulfill({
        status: 409,
        json: {
          error: {
            code: "conflict",
            message: "Another cashfree card contract is already in force.",
          },
        },
      });
    }
    const row: Row = { ...body, account_ref: "default", id: id ?? `c${next++}` };
    if (id) {
      rows[rows.findIndex((existing) => existing.id === id)] = row;
    } else {
      rows.push(row);
    }
    return route.fulfill({ status: id ? 200 : 201, json: row });
  });
}

const upi: Row = {
  id: "c1",
  provider: "razorpay",
  account_ref: "default",
  method: "upi",
  card_network: null,
  card_type: null,
  rate_bps: 0,
  fixed_fee_minor: 0,
  tax_rate_bps: 1800,
  tax_on_refunded_fee: false,
  allowed_fee_types: ["payment"],
  effective_from: "2026-01-01",
  effective_to: null,
};

test("a contract is added, edited and deleted", async ({ page }) => {
  const sent: Record<string, unknown>[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/fee-contracts") && request.method() !== "GET") {
      sent.push({ method: request.method(), body: request.postDataJSON() });
    }
  });
  await fakeApi(page, [{ ...upi }]);
  await page.goto("/contracts");

  await expect(page.getByRole("heading", { name: "Fee contracts" })).toBeVisible();
  const rows = page.getByTestId("contract-row");
  await expect(rows).toHaveCount(1);
  await expect(rows.first()).toContainText("Razorpay · UPI");
  await expect(rows.first()).toContainText("0.00%");

  // Add Cashfree Amex at 2.00% with a ₹3 fixed fee.
  await page.getByLabel("Provider", { exact: true }).selectOption("cashfree");
  await page.getByLabel("Card network").selectOption("amex");
  await page.getByLabel("Rate (%)").fill("2");
  await page.getByLabel("Fixed fee per payment (₹)").fill("3");
  await page.getByLabel("In force from").fill("2026-01-01");
  await page.getByRole("button", { name: "Add contract" }).click();

  await expect(page.locator("form").getByRole("status")).toContainText("Added Cashfree · Card · Amex");
  await expect(rows).toHaveCount(2);
  await expect(rows.nth(1)).toContainText("2.00% + ₹3.00");
  expect(sent[0]).toEqual({
    method: "POST",
    body: {
      provider: "cashfree",
      method: "card",
      card_network: "amex",
      card_type: null,
      rate_bps: 200,
      fixed_fee_minor: 300,
      tax_rate_bps: 1800,
      tax_on_refunded_fee: false,
      allowed_fee_types: ["payment"],
      effective_from: "2026-01-01",
      effective_to: null,
    },
  });

  // Edit it: the form fills with what is stored.
  await page.getByRole("button", { name: "Edit Cashfree Card · Amex" }).click();
  await expect(page.getByLabel("Rate (%)")).toHaveValue("2.00");
  await page.getByLabel("Rate (%)").fill("1.95");
  await page.getByRole("button", { name: "Save changes" }).click();
  await expect(rows.nth(1)).toContainText("1.95% + ₹3.00");
  expect(sent[1]).toMatchObject({ method: "PUT", body: { rate_bps: 195 } });

  // Delete asks once more before it goes.
  await page.getByRole("button", { name: "Delete Cashfree Card · Amex" }).click();
  await page.getByRole("button", { name: "Confirm delete" }).click();
  await expect(rows).toHaveCount(1);
});

test("the form says what is wrong before anything is sent", async ({ page }) => {
  let writes = 0;
  page.on("request", (request) => {
    if (request.url().includes("/fee-contracts") && request.method() !== "GET") writes += 1;
  });
  await fakeApi(page, []);
  await page.goto("/contracts");

  await expect(page.getByText("No contracts yet.")).toBeVisible();
  await page.getByLabel("Rate (%)").fill("2.155");
  await page.getByRole("button", { name: "Add contract" }).click();

  await expect(page.getByText("Enter a rate from 0 to 100")).toBeVisible();
  await expect(page.getByText("Choose the date the contract starts.")).toBeVisible();
  expect(writes).toBe(0);
});

test("a refusal from the API is shown in its own words", async ({ page }) => {
  await fakeApi(page, []);
  await page.goto("/contracts");

  await page.getByLabel("Rate (%)").fill("9.99");
  await page.getByLabel("In force from").fill("2026-01-01");
  await page.getByRole("button", { name: "Add contract" }).click();

  await expect(page.locator("form").getByRole("alert")).toHaveText(
    "Another cashfree card contract is already in force.",
  );
});

test("the card fields appear only for cards, and a marketplace allows commission", async ({
  page,
}) => {
  await fakeApi(page, []);
  await page.goto("/contracts");

  await expect(page.getByLabel("Card network")).toBeVisible();
  await page.getByLabel("Payment method").selectOption("commission");

  await expect(page.getByLabel("Card network")).toHaveCount(0);
  await expect(page.getByLabel("Fees the contract allows")).toHaveValue("commission");
});
