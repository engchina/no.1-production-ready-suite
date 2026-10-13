import { defineConfig, devices } from "@playwright/test";

import { mobileProjectScope } from "../../platform/scripts/e2e-impact/mobile-scope";

const playwrightPort = process.env.PLAYWRIGHT_PORT ?? "3101";
const playwrightBaseUrl = `http://127.0.0.1:${playwrightPort}`;

export default defineConfig({
  testDir: "./tests/e2e",
  // 対象の dev サーバ（再利用したものを含む）が hermetic であることを確認してから開始し、
  // 実行前後で利用者の実環境の設定ファイルが変わっていないことを検査する。
  globalSetup: "./tests/e2e/global-setup.ts",
  timeout: 30_000,
  // RAG と同じく CI だけ retries 2。遅い runner で 1 件の一時的な遅延が shard 全体を赤にしないため（#175）。
  // retry で通ったテストは flaky として report に残る。nightly（e2e-nightly.yml）でも拾う。
  retries: process.env.CI ? 2 : 0,
  // CI（4 vCPU の runner）は 2 worker。ci.yml・e2e-nightly.yml は `--workers` で上書きしない（#1489）。
  workers: process.env.CI ? 2 : 4,
  expect: {
    timeout: 8_000,
  },
  fullyParallel: true,
  // CI は失敗・flaky を PR の注釈（github）に、log を dot にし、json は nightly の flaky の一覧に使う
  // （platform/scripts/playwright_report_summary.py。#1591）。
  reporter: process.env.CI
    ? [["github"], ["dot"], ["json", { outputFile: "playwright-report/results.json" }]]
    : "list",
  use: {
    baseURL: playwrightBaseUrl,
    trace: "retain-on-failure",
  },
  webServer: {
    command: `npm run dev -- --host 127.0.0.1 --port ${playwrightPort}`,
    url: playwrightBaseUrl,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
    // e2e は全 API を page.route で mock する前提(hermetic)。未モック API は
    // Vite 側の 404 middleware で終端し、実 backend / Oracle 環境へ接続しない。
    env: { ...process.env, PLAYWRIGHT_HERMETIC_API: "1" },
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
      // PR の CI は E2E_MOBILE_SCOPE=responsive で、画面幅に依存するテスト（@responsive など）だけを実行する。
      // nightly とローカルは全件（#1582。platform/scripts/e2e-impact/mobile-scope.ts）。
      ...mobileProjectScope(),
    },
  ],
});
