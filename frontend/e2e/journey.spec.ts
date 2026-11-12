import { expect, test, type Page } from "@playwright/test";

/**
 * One procurement journey driven only through the UI, against real services:
 * the catalogue is entered through the Vendor Intelligence forms, the agents use the real
 * Claude API, and checkout loads the real PayPal sandbox SDK. Paying needs a person
 * to sign in to a PayPal sandbox buyer account, so the test stops at the PayPal button.
 */

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

test("catalogue → agents (Claude) → approval → PayPal sandbox checkout", async ({ page }) => {
  test.setTimeout(420_000);

  // 1. Admin builds the catalogue: a vendor, a product with comparable specs, and the vendor's offer.
  await signInAs(page, "Ade Admin");
  await page.goto("/vendors");
  await expect(page.getByText("Build your catalogue")).toBeVisible();

  await page.getByRole("button", { name: "Add vendor" }).first().click();
  const vendor = page.getByRole("dialog", { name: "Add vendor" });
  await vendor.getByLabel("Company name").fill("Northwind IT Supply");
  await vendor.getByLabel("Contact email").fill("sales@northwind.example");
  await vendor.getByLabel(/Country/).fill("US");
  await vendor.getByRole("button", { name: "Add vendor" }).click();
  await expect(page.getByText("Northwind IT Supply added")).toBeVisible();

  await page.getByRole("button", { name: "Add product" }).first().click();
  const product = page.getByRole("dialog", { name: "Add product" });
  await product.getByLabel("Product name").fill("ProBook 14 Laptop");
  await product.getByLabel("SKU").fill("LAP-PRO-14");
  await product.getByLabel(/Category/).fill("laptops");
  await product.getByLabel("Description").fill("14 inch business laptop");
  await product.getByLabel("Spec name").first().fill("ram_gb");
  await product.getByLabel("Spec value").first().fill("16");
  await product.getByRole("button", { name: "Add product" }).click();
  await expect(page.getByText("ProBook 14 Laptop added")).toBeVisible();

  await page.getByRole("button", { name: "Offer", exact: true }).click();
  const offer = page.getByRole("dialog", { name: "Add offer" });
  await offer.getByLabel("Vendor").selectOption({ label: "Northwind IT Supply" });
  await offer.getByLabel(/Unit price/).fill("1149");
  await offer.getByLabel("In stock").fill("120");
  await offer.getByLabel("Delivery (days)").fill("5");
  await offer.getByRole("button", { name: "Add offer" }).click();
  await expect(page.getByText("Offer from Northwind IT Supply added")).toBeVisible();
  await expect(page.getByRole("cell", { name: "Northwind IT Supply lowest" })).toBeVisible();
  await expect(page.getByRole("cell", { name: "$1,149.00" })).toBeVisible();

  // 2. Requester submits a plain-language request; the real model reads it and the agents run.
  await signOut(page);
  await signInAs(page, "Riley Requester");
  await expect(page.getByRole("link", { name: "Approval Center" })).toHaveCount(0); // role-based navigation
  await page.getByRole("link", { name: "AI Workspace" }).first().click();
  await page.getByLabel("What do you need?").fill("10 laptops with at least 16GB RAM, budget $15,000");
  await page.getByRole("button", { name: "Submit & run agents" }).click();
  await expect(page).toHaveURL(/\/workspace\/[0-9a-f-]{36}$/);
  await expect(page.getByText("Awaiting approval").first()).toBeVisible({ timeout: 300_000 });
  await expect(page.getByText("$11,490.00").first()).toBeVisible(); // 10 × 1,149.00 from the database price
  const requestUrl = page.url();

  // 3. Approver approves in the Approval Center.
  await signOut(page);
  await signInAs(page, "Avery Approver");
  await page.getByRole("link", { name: "Approval Center" }).first().click();
  await expect(page.getByText("AI recommendation")).toBeVisible();
  await page.getByRole("button", { name: "Approve" }).click();
  await expect(page.getByText(/PO-.* approved/)).toBeVisible();

  // 4. Requester sees the real PayPal sandbox button (no simulated checkout exists).
  await signOut(page);
  await signInAs(page, "Riley Requester");
  await page.goto(requestUrl);
  await expect(page.getByText("PayPal sandbox")).toBeVisible({ timeout: 30_000 });
  await expect(page.locator("paypal-button")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText("simulated")).toHaveCount(0);
});

test("signed-out users get the sign-in page and the API refuses them", async ({ page }) => {
  await page.goto("/workspace");
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  const res = await page.request.get("/api/v1/procurement/requests");
  expect(res.status()).toBe(401);
});
