import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  retries: 0,
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? "http://127.0.0.1:8080",
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "desktop-chromium",
      testIgnore: "hosted-auth.spec.ts",
      use: { ...devices["Desktop Chrome"] },
    },
    {
      name: "mobile-chromium",
      testIgnore: "hosted-auth.spec.ts",
      use: { ...devices["Pixel 7"] },
    },
    {
      name: "visual-desktop",
      testIgnore: "hosted-auth.spec.ts",
      use: {
        browserName: "chromium",
        viewport: { width: 1586, height: 992 },
        deviceScaleFactor: 1,
        colorScheme: "light",
        reducedMotion: "reduce",
      },
    },
    {
      name: "hosted-chromium",
      testMatch: "hosted-auth.spec.ts",
      use: {
        ...devices["Desktop Chrome"],
        ignoreHTTPSErrors: true,
      },
    },
    {
      name: "hosted-mobile-chromium",
      testMatch: "hosted-auth.spec.ts",
      use: {
        ...devices["Pixel 7"],
        ignoreHTTPSErrors: true,
      },
    },
  ],
});
