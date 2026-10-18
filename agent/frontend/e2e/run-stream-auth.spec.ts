import type { Page } from "@playwright/test";

import { MOCK_NOW, expect, test, type MockApi } from "./fixtures/mock-api";

// Run のイベントの購読（SSE）と、Run の操作（REST）の確認（#215 / #814）。
// push は SSE、取消・再開・承認は REST だけ（WebSocket の経路は持たない。#2260）。
// SSE が切れたら画面は自動の再接続をやめて停止を示し、利用者の「再接続」でだけつなぎ直す（無限に再接続しない）。

const VIEWPORTS = [
  { name: "desktop", width: 1280, height: 800 },
  { name: "mobile-375", width: 375, height: 812 },
] as const;

function seedRunningRun(mockApi: MockApi) {
  mockApi.state.runs.push({
    id: "run-stream",
    goal: "購読を確認する",
    agent_id: "default",
    // 「再開」を出す Run（承認が決まり、保存した SDK の状態から再開を待つ組み込み Runtime の Run。#1119）。
    runtime_id: "builtin",
    status: "queued",
    steps: [],
    events: [],
    approvals: [],
    artifacts: [],
    pending_tool_calls: [],
    metadata: { _builtin_sdk_state: "{}" },
    created_at: MOCK_NOW,
    updated_at: MOCK_NOW,
  });
}

/** ストリームの状態（「技術的な詳細」の中。#1955）。 */
function streamCard(page: Page) {
  return page.getByTestId("run-stream");
}

async function openStreamControls(page: Page) {
  await page.getByTestId("run-technical-section").locator("summary").first().click();
  await expect(streamCard(page)).toBeVisible();
}

async function expectNoPageOverflow(page: Page) {
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth))
    .toBeLessThanOrEqual(0);
}

for (const viewport of VIEWPORTS) {
  test.describe(`Run イベントの購読と操作 (${viewport.name})`, () => {
    test.beforeEach(async ({ page, mockApi }) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      seedRunningRun(mockApi);
    });

    test("Run の操作は REST で送り、押した操作だけが応答まで処理中になる", async ({ page, mockApi }) => {
      // 応答を止めて、処理中の表示を確かめてから fixture の応答へ流す。
      let release: () => void = () => undefined;
      const gate = new Promise<void>((resolve) => {
        release = resolve;
      });
      await page.route("**/api/runs/run-stream/resume", async (route) => {
        await gate;
        await route.fallback();
      });

      await page.goto("/runs?id=run-stream");
      await expect(page.getByText("購読を確認する").first()).toBeVisible();
      const bar = page.getByTestId("run-object-actions");
      const resume = bar.getByRole("button", { name: "再開", exact: true });
      await resume.click();

      // 押した「再開」だけが処理中、他の操作は押せない（messaging.md §3.7）。
      await expect(resume).toHaveAttribute("aria-busy", "true");
      await expect(bar.getByRole("button", { name: "再実行", exact: true })).toBeDisabled();
      release();
      await expect.poll(() => mockApi.lastRequest("POST", "/api/runs/run-stream/resume")).toBeTruthy();
      await expect(resume).not.toHaveAttribute("aria-busy", "true");
      await expectNoPageOverflow(page);
    });

    test("SSE の接続が切れたら自動の再接続をやめ、停止を示して再接続できる", async ({ page, mockApi }) => {
      await page.goto("/runs?id=run-stream");
      await expect(page.getByText("購読を確認する").first()).toBeVisible();

      await openStreamControls(page);
      // 購読は SSE だけで、方式の切り替えは出さない（#2260）。
      await expect(streamCard(page).getByRole("button", { name: "WebSocket" })).toHaveCount(0);
      await expect(page.getByRole("group", { name: "ストリーム方式" })).toHaveCount(0);
      // e2e の SSE は空の stream を返して閉じる（fixture）。状態は色だけでなく文字でも示す。
      await expect(page.getByTestId("run-stream-stopped")).toContainText("SSE の接続が切れたため");
      await expect(streamCard(page).getByText("停止", { exact: true })).toBeVisible();
      const eventRequests = () =>
        mockApi.requests.filter((request) => request.path === "/api/runs/run-stream/events").length;
      const before = eventRequests();
      await page.waitForTimeout(1_500);
      expect(eventRequests()).toBe(before);
      await expectNoPageOverflow(page);

      await page.getByRole("button", { name: "再接続" }).click();
      await expect.poll(eventRequests).toBeGreaterThan(before);
    });
  });
}
