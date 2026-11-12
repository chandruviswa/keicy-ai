import { expect, test, type Page } from "@playwright/test";

/** Phase 12.5: every screen fits the viewport — phone, tablet and wide desktop — with navigation that suits it. */

const SCREENS = ["/", "/workspace", "/vendors", "/purchase-orders", "/approvals", "/payments", "/audit"];

async function noHorizontalScroll(page: Page, where: string) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow, `${where} should not scroll sideways`).toBeLessThanOrEqual(1);
}

test("all screens fit and navigation adapts", async ({ page }) => {
  const width = page.viewportSize()!.width;
  await page.goto("/");
  await noHorizontalScroll(page, "sign-in");
  await page.getByRole("button", { name: /Ade Admin/ }).click();
  await expect(page.getByRole("navigation", { name: "Breadcrumb" })).toBeVisible({ timeout: 15_000 }); // only inside the app

  for (const path of SCREENS) {
    await page.goto(path);
    await page.waitForLoadState("networkidle");
    await expect(page.getByRole("navigation", { name: "Breadcrumb" })).toBeVisible();
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await noHorizontalScroll(page, path);
  }

  if (width < 768) {
    // Phones: the sidebar is a drawer behind a menu button.
    await expect(page.getByRole("link", { name: "Payment Center" })).toBeHidden();
    await page.getByRole("button", { name: "Open menu" }).click();
    await page.getByRole("link", { name: "Payment Center" }).click();
  } else {
    // Tablets get an icon rail, desktops the full sidebar — navigation is always on screen.
    await expect(page.getByRole("button", { name: "Open menu" })).toBeHidden();
    await page.getByRole("link", { name: "Payment Center" }).click();
  }
  await expect(page.getByRole("heading", { name: "Payment Center" })).toBeVisible();
});
