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
  // RAG / NL2SQL と同じく CI だけ retries 2（#339）。遅い runner で 1 件の一時的な遅延が CI 全体を赤にしないため。
  // retries が無いと trace: "on-first-retry" も記録されない。
  retries: process.env.CI ? 2 : 0,
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
  // RAG / NL2SQL と同じく desktop と mobile-375 の 2 project（#823。名前は NL2SQL にそろえる）。
  // mobile-375 はタッチ端末（Pixel 5、375×812、isMobile）で、操作部品が 44px になる。
  projects: [
    {
      name: "desktop",
      use: { ...devices["Desktop Chrome"] },
    },
    {
      name: "mobile-375",
      use: {
        ...devices["Pixel 5"],
        viewport: { width: 375, height: 812 },
        isMobile: true,
      },
      // 画面幅に関係しない spec（vite の設定・フォントの読み込み・権限カタログの一致）は desktop だけで実行する。
      testIgnore: ["**/vite-config.spec.ts", "**/self-hosted-fonts.spec.ts", "**/permission-catalog.spec.ts"],
      // spec の中で viewport を回しているテスト（題名に「(desktop」「（mobile）」「mobile-375: 」や「1920px」を含む）は、
      // その中で 375px を確かめているので二重に実行しない。
      grepInvert: /[(（](desktop|mobile)|(desktop|mobile-375): |\d{4}px/,
    },
  ],
});
