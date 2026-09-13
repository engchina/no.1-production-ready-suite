import { defineConfig, devices } from "@playwright/test";

const skipWebServer = process.env.PLAYWRIGHT_SKIP_WEB_SERVER === "1";
// 開発用 dev サーバ（3000）と衝突しない既定ポート。
const port = process.env.PLAYWRIGHT_PORT ?? "3100";
const baseURL = process.env.PLAYWRIGHT_BASE_URL ?? `http://127.0.0.1:${port}`;

export default defineConfig({
  testDir: "./e2e",
  // 起動した dev サーバが hermetic であることを確認し、実行前後で利用者の実環境の
  // 設定ファイルが変わっていないことを検査する。
  globalSetup: "./e2e/global-setup.ts",
  timeout: 30_000,
  workers: 1,
  // CI はヘッドレス環境のタイミング差で e2e が稀に flake するため retry する。
  // retries 未設定だと trace: "on-first-retry" も機能せず、1 件の flake で CI 全体が赤になる。
  retries: process.env.CI ? 2 : 0,
  expect: {
    timeout: 5_000,
  },
  reporter: "list",
  use: {
    baseURL,
    trace: "on-first-retry",
  },
  webServer: skipWebServer
    ? undefined
    : {
        command: `npm run dev -- --host 127.0.0.1 --port ${port} --strictPort`,
        url: baseURL,
        // 既存の（hermetic でない）dev サーバを再利用すると実 backend へ proxy されるため、常に起動する。
        reuseExistingServer: false,
        timeout: 120_000,
        // e2e は全 API を page.route で mock する前提（hermetic）。未モック API は Vite 側の
        // 404 middleware で終端し、実 backend / Oracle 環境へ接続しない。
        env: { ...process.env, PLAYWRIGHT_HERMETIC_API: "1", BACKEND_URL: "http://127.0.0.1:9" },
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
