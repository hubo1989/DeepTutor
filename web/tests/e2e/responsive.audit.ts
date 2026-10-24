import { test, expect } from "@playwright/test";

// This fork's deployment default UI language is Chinese; force English so
// the accessible names asserted below are stable in every environment.
test.beforeEach(async ({ page }) => {
  await page.addInitScript(() =>
    localStorage.setItem("deeptutor-language", "en"),
  );
});

test.describe("Responsive shell", () => {
  test("keeps the page inside the viewport", async ({ page }) => {
    await page.goto("/chat", { waitUntil: "networkidle" });
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
    await page.goto("/chat", { waitUntil: "networkidle" });
    const toggle = page.getByTestId("mobile-nav-toggle");

    // The audit project always runs Desktop Chrome: above the lg (1024px)
    // breakpoint the toggle stays hidden.
    await expect(toggle).toBeHidden();

    // Below it the shell is CSS-responsive, so shrinking the viewport is
    // enough for the drawer toggle to appear.
    await page.setViewportSize({ width: 390, height: 844 });
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
