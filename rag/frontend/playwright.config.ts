import { defineConfig, devices } from "@playwright/test";

import { ciRetries, ciWorkers } from "../../platform/scripts/e2e-impact/ci-run";
import { mobileProjectScope } from "../../platform/scripts/e2e-impact/mobile-scope";

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
  // CI（4 vCPU の runner）は CI_WORKERS の worker で spec のファイル単位で並べる（#1489・#1593）。手元は従来どおり 1 worker。
  workers: ciWorkers(1),
  // CI はヘッドレス環境のタイミング差で e2e が稀に flake するため retry する。PR は 1、main の push と nightly は 2
  // （#1593。platform/scripts/e2e-impact/ci-run.ts）。
  // retries 未設定だと trace: "on-first-retry" も機能せず、1 件の flake で CI 全体が赤になる。
  retries: ciRetries(),
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
      // PR の CI は E2E_MOBILE_SCOPE=responsive で、画面幅に依存するテスト（@responsive など）だけを実行する。
      // nightly とローカルは全件（#1582。platform/scripts/e2e-impact/mobile-scope.ts）。
      ...mobileProjectScope(),
    },
  ],
});
