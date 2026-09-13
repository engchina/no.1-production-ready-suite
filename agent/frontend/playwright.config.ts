import { defineConfig, devices } from "@playwright/test";
import { fileURLToPath } from "node:url";

const frontendPort = process.env.PLAYWRIGHT_PORT ?? "3042";
const frontendUrl = `http://127.0.0.1:${frontendPort}`;
const frontendCwd = fileURLToPath(new URL(".", import.meta.url));

export default defineConfig({
  testDir: "./e2e",
  // 起動した dev サーバが hermetic であることを確認し、実行前後で利用者の実環境の
  // 設定ファイルが変わっていないことを検査する。
  globalSetup: "./e2e/global-setup.ts",
  fullyParallel: false,
  workers: 1,
  timeout: 30_000,
  expect: {
    timeout: 7_500,
  },
  use: {
    baseURL: frontendUrl,
    trace: "on-first-retry",
  },
  // e2e は実 backend を起動しない。すべての API は e2e/fixtures/mock-api.ts の page.route で
  // mock し、未モックの /api は Vite の hermetic middleware が 404 で終端する。
  webServer: {
    command: `npm run dev -- --host 127.0.0.1 --port ${frontendPort} --strictPort`,
    cwd: frontendCwd,
    url: frontendUrl,
    // 既存の（hermetic でない）dev サーバを再利用すると実 backend へ proxy されるため、常に起動する。
    reuseExistingServer: false,
    timeout: 30_000,
    env: {
      ...process.env,
      PLAYWRIGHT_HERMETIC_API: "1",
      // 念のため proxy 先も到達不能な宛先にしておく（hermetic では proxy 自体を無効化している）。
      BACKEND_URL: "http://127.0.0.1:9",
    },
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
