import { expect, test, type Page } from "@playwright/test";

/** Phase 12.4: one complete procurement-to-payment journey, driven only through the UI. */

async function signInAs(page: Page, name: "Riley Requester" | "Avery Approver" | "Ade Admin") {
  await page.goto("/");
  await page.getByRole("button", { name: new RegExp(name) }).click();
  await expect(page.getByRole("button", { name: "Sign out" }).first()).toBeVisible(); // only exists once signed in
  await expect(page.getByText(name).first()).toBeVisible();
}

async function signOut(page: Page) {
  await page.getByRole("button", { name: "Sign out" }).first().click();
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
}

test("request → agents → approval → PayPal → reconciliation → receipt", async ({ page }) => {
  // 1. Requester submits a plain-language request.
  await signInAs(page, "Riley Requester");
  await expect(page.getByRole("link", { name: "Approval Center" })).toHaveCount(0); // role-based navigation
  await page.getByRole("link", { name: "AI Workspace" }).first().click();
  await page.getByLabel("What do you need?").fill("10 laptops, 16GB RAM, under $15,000 by Dec 1");
  await page.getByRole("button", { name: "Submit & run agents" }).click();

  // 2. Agents run live: requirements, research, evaluation, PO, finance.
  await expect(page).toHaveURL(/\/workspace\/[0-9a-f-]{36}$/);
  await expect(page.getByText("Awaiting approval").first()).toBeVisible({ timeout: 60_000 });
  await expect(page.getByRole("heading", { name: "10 × laptop" })).toBeVisible(); // title derived from the request
  await expect(page.getByText("RAM ≥ 16 GB")).toBeVisible();
  await expect(page.getByRole("row", { name: /Fabrikam Office.*recommended/ })).toBeVisible();
  await expect(page.getByText("$11,890.00").first()).toBeVisible();
  await expect(page.getByText(/finance agent finished/)).toBeVisible();
  await expect(page.getByText("Waiting for an approver to review this purchase order.")).toBeVisible();
  const requestUrl = page.url();

  // 3. Refresh: everything is restored from the server (state + replayed live events).
  await page.reload();
  await expect(page.getByText("Awaiting approval").first()).toBeVisible();
  await expect(page.getByText(/Approval required for PO-/)).toBeVisible();

  // 4. Approver reviews the AI recommendation and approves in the Approval Center.
  await signOut(page);
  await signInAs(page, "Avery Approver");
  await page.getByRole("link", { name: "Approval Center" }).first().click();
  await expect(page.getByText("AI recommendation")).toBeVisible();
  await expect(page.getByText("Budget validation")).toBeVisible();
  await expect(page.getByText("$15,000.00").first()).toBeVisible();
  await page.getByRole("button", { name: "Approve" }).click();
  await expect(page.getByText(/PO-.* approved/)).toBeVisible(); // toast
  await expect(page.getByRole("cell", { name: "Avery Approver" }).first()).toBeVisible(); // history

  // 5. Requester pays with PayPal (simulator) — capture, verification and reconciliation follow.
  await signOut(page);
  await signInAs(page, "Riley Requester");
  await page.goto(requestUrl);
  await expect(page.getByText("Approved").first()).toBeVisible();
  await page.getByRole("button", { name: /Pay with PayPal/ }).click();
  await expect(page.getByText(/Receipt RCPT-/)).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText("Completed").first()).toBeVisible();
  await expect(page.getByText("Workflow completed")).toBeVisible();

  // 6. The rest of the app reflects it.
  await page.getByRole("link", { name: "Payment Center" }).first().click();
  await expect(page.getByRole("button", { name: "Receipt" })).toBeVisible();
  await page.getByRole("button", { name: "Receipt" }).click();
  await expect(page.getByText(/^RCPT-/)).toBeVisible();
  await page.keyboard.press("Escape");
  await page.goto("/purchase-orders");
  await expect(page.getByRole("cell", { name: "Paid", exact: true }).first()).toBeVisible();
  await page.goto("/");
  await expect(page.getByText("1 completed purchase", { exact: true })).toBeVisible();
  await expect(page.getByText("$11,890.00").first()).toBeVisible();

  // 7. Admin can trace it in Intelligence & Audit.
  await signOut(page);
  await signInAs(page, "Ade Admin");
  await page.getByRole("link", { name: "Intelligence & Audit" }).first().click();
  await expect(page.getByRole("cell", { name: "reconciliation" }).first()).toBeVisible();
  await page.getByRole("tab", { name: "Models" }).click();
  await expect(page.getByRole("cell", { name: "mock" }).first()).toBeVisible();
  await page.getByRole("tab", { name: "Audit trail" }).click();
  await expect(page.getByText("payment.completed").first()).toBeVisible();
});

test("clarification loop: missing budget → edit → re-run", async ({ page }) => {
  await signInAs(page, "Riley Requester");
  await page.goto("/workspace");
  await page.getByLabel("What do you need?").fill("4 monitors for the design team");
  await page.getByRole("button", { name: "Submit & run agents" }).click();
  await expect(page.getByText("The agents need a bit more information")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText("What is the maximum budget for this purchase?")).toBeVisible();
  await page.getByLabel("Budget").fill("3000");
  await page.getByRole("button", { name: "Update & re-run" }).click();
  await expect(page.getByText("Awaiting approval").first()).toBeVisible({ timeout: 60_000 });
});

test("requests that can't be fulfilled are rejected with a reason", async ({ page }) => {
  await signInAs(page, "Riley Requester");
  await page.goto("/workspace");
  await page.getByLabel("What do you need?").fill("500 docking stations, budget $200,000");
  await page.getByRole("button", { name: "Submit & run agents" }).click();
  await expect(page.getByText(/Rejected:.*No vendor can currently supply 500/)).toBeVisible({ timeout: 30_000 });
});

test("signed-out users get the sign-in page and the API refuses them", async ({ page }) => {
  await page.goto("/workspace");
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  const res = await page.request.get("/api/v1/procurement/requests");
  expect(res.status()).toBe(401);
});
