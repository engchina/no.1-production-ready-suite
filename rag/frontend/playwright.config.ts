import { defineConfig, devices } from "@playwright/test";

const skipWebServer = process.env.PLAYWRIGHT_SKIP_WEB_SERVER === "1";

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  workers: 1,
  expect: {
    timeout: 5_000,
  },
  reporter: "list",
  use: {
    baseURL: "http://127.0.0.1:3000",
    trace: "on-first-retry",
  },
  webServer: skipWebServer
    ? undefined
    : {
        command: "npm run dev -- --host 127.0.0.1 --port 3000",
        url: "http://127.0.0.1:3000",
        reuseExistingServer: !process.env.CI,
        timeout: 120_000,
      },
  projects: [
    {
      name: "desktop",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } },
    },
    {
      name: "mobile",
      use: { ...devices["Pixel 5"], viewport: { width: 375, height: 812 } },
    },
  ],
});
