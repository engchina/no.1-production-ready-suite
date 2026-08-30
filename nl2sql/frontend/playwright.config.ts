import { defineConfig, devices } from "@playwright/test";

const playwrightPort = process.env.PLAYWRIGHT_PORT ?? "3101";
const playwrightBaseUrl = `http://127.0.0.1:${playwrightPort}`;

export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 30_000,
  workers: process.env.CI ? 2 : 4,
  expect: {
    timeout: 8_000,
  },
  fullyParallel: true,
  reporter: process.env.CI ? "dot" : "list",
  use: {
    baseURL: playwrightBaseUrl,
    trace: "retain-on-failure",
  },
  webServer: {
    command: `npm run dev -- --host 127.0.0.1 --port ${playwrightPort}`,
    url: playwrightBaseUrl,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
    // e2e は全 API を page.route で mock する前提(hermetic)。未モックの API が
    // ローカルで起動中の実バックエンドへ proxy されると 401 が返り AuthProvider が
    // ログイン画面へ戻してしまうため、proxy 先を必ず接続不能な port に固定して
    // CI(バックエンド無し)と同じ失敗挙動に揃える。
    env: { ...process.env, BACKEND_URL: "http://127.0.0.1:9" },
  },
  projects: [
    {
      name: "desktop",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 1280, height: 900 },
      },
    },
    {
      name: "mobile-375",
      use: {
        ...devices["Pixel 5"],
        viewport: { width: 375, height: 812 },
        isMobile: true,
      },
    },
  ],
});
