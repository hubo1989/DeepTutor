import { test, expect } from "@playwright/test";

test.describe("Responsive shell", () => {
  test("keeps the page inside the viewport", async ({ page }) => {
    await page.goto("/home", { waitUntil: "networkidle" });
    await expect(page.locator("body")).toBeVisible();

    const overflow = await page.evaluate(() => ({
      viewport: window.innerWidth,
      documentWidth: document.documentElement.scrollWidth,
      bodyWidth: document.body.scrollWidth,
    }));
    expect(overflow.documentWidth).toBeLessThanOrEqual(overflow.viewport + 1);
    expect(overflow.bodyWidth).toBeLessThanOrEqual(overflow.viewport + 1);
  });

  test("uses an accessible navigation drawer below desktop width", async ({
    page,
  }) => {
    await page.goto("/home", { waitUntil: "networkidle" });
    const toggle = page.getByTestId("mobile-nav-toggle");

    if (test.info().project.name === "responsive-desktop") {
      await expect(toggle).toBeHidden();
      return;
    }

    await expect(toggle).toBeVisible();
    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-expanded", "true");
    const drawer = page.getByTestId("responsive-sidebar-drawer");
    await expect(drawer).toHaveAttribute("aria-modal", "true");
    await expect(page.locator("main")).toHaveAttribute("inert");

    await page.keyboard.press("Escape");
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
    await expect(toggle).toBeFocused();
  });
});
