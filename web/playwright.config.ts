import { defineConfig, devices } from "@playwright/test";

const BASE_URL =
  process.env.WEB_BASE_URL ||
  process.env.NEXT_PUBLIC_API_BASE ||
  "http://localhost:3000";
const SERIAL_MODE = process.env.PW_SERIAL === "1";

export default defineConfig({
  testDir: "./tests",
  fullyParallel: !SERIAL_MODE,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: SERIAL_MODE ? 1 : undefined,
  reporter: [["html", { open: "never" }], ["list"]],
  use: {
    baseURL: BASE_URL,
    trace: "on-first-retry",
  },
  projects: [
    {
      name: "ui-audit",
      testMatch: "**/*.audit.ts",
      use: { ...devices["Desktop Chrome"] },
    },
    {
      name: "responsive-phone",
      testMatch: "**/responsive.audit.ts",
      use: { ...devices["Pixel 5"] },
    },
    {
      name: "responsive-tablet",
      testMatch: "**/responsive.audit.ts",
      use: { viewport: { width: 768, height: 1024 }, isMobile: true },
    },
    {
      name: "responsive-desktop",
      testMatch: "**/responsive.audit.ts",
      use: { viewport: { width: 1280, height: 800 } },
    },
  ],
});
