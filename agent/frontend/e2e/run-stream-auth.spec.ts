import type { Page, WebSocketRoute } from "@playwright/test";

import { MOCK_NOW, expect, test, type MockApi } from "./fixtures/mock-api";

// Run のイベント購読（WebSocket / SSE）の認証・権限による停止（#215）。
// backend は権限・認証で拒否すると `{type: "error", error_code: "rbac.*" | "auth.*"}` を送ってから close 1008 で閉じる。
// 画面は再接続をやめて理由を示し、利用者の「再接続」でだけつなぎ直す（無限に再接続しない）。

const VIEWPORTS = [
  { name: "desktop", width: 1280, height: 800 },
  { name: "mobile-375", width: 375, height: 812 },
] as const;

const WS_URL = /\/api\/runs\/[^/]+\/events\/ws/;

function seedRunningRun(mockApi: MockApi) {
  mockApi.state.runs.push({
    id: "run-stream",
    goal: "購読を確認する",
    agent_id: "default",
    runtime_id: "legacy-native",
    binding_id: null,
    external_run_id: null,
    external_cursor: null,
    runtime_capabilities: {
      stream_events: true,
      cancel: true,
      artifacts: true,
      approvals: true,
      skill_sync: false,
      mcp_sync: false,
    },
    status: "running",
    steps: [],
    events: [],
    approvals: [],
    artifacts: [],
    pending_tool_calls: [],
    metadata: {},
    created_at: MOCK_NOW,
    updated_at: MOCK_NOW,
  });
}

/** ストリームの card（見出しの説明と方式の切替の両方を含む最も内側の要素）。 */
function streamCard(page: Page) {
  return page
    .locator("div")
    .filter({ has: page.getByRole("group", { name: "ストリーム方式" }) })
    .filter({ hasText: "Run event の受信方式" })
    .last();
}

async function useWebSocketMode(page: Page) {
  await page.getByRole("group", { name: "ストリーム方式" }).getByRole("button", { name: "WebSocket" }).click();
}

async function expectNoPageOverflow(page: Page) {
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth))
    .toBeLessThanOrEqual(0);
}

for (const viewport of VIEWPORTS) {
  test.describe(`Run イベントの購読と権限 (${viewport.name})`, () => {
    test.beforeEach(async ({ page, mockApi }) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      seedRunningRun(mockApi);
    });

    test("WebSocket が権限で拒否され close 1008 になったら再接続せず、理由と再接続の操作を出す", async ({ page }) => {
      const connections: WebSocketRoute[] = [];
      await page.routeWebSocket(WS_URL, (ws) => {
        connections.push(ws);
        ws.send(JSON.stringify({ type: "error", error_code: "rbac.agent_forbidden", message: "agent access denied" }));
        ws.close({ code: 1008, reason: "forbidden" });
      });

      await page.goto("/runs");
      await expect(page.getByText("購読を確認する").first()).toBeVisible();
      await useWebSocketMode(page);

      await expect(page.getByTestId("run-stream-stopped")).toHaveText("この Run のイベントを購読する権限がありません。");
      await expect(streamCard(page).getByText("停止", { exact: true })).toBeVisible();
      // 再接続の待ち時間（最初は 0.5 秒）を過ぎても、つなぎ直さない。
      await page.waitForTimeout(2_000);
      expect(connections).toHaveLength(1);
      await expectNoPageOverflow(page);

      // 利用者の操作でだけつなぎ直す。
      await page.getByRole("button", { name: "再接続" }).click();
      await expect.poll(() => connections.length).toBe(2);
      await expect(page.getByTestId("run-stream-stopped")).toBeVisible();
      expect(connections[0].url()).toContain("/api/runs/run-stream/events/ws");
    });

    test("WebSocket が認証（auth.*）で拒否されたらログインを確かめ、失効していればログイン画面へ移す", async ({
      page,
      mockApi,
    }) => {
      let connections = 0;
      await page.routeWebSocket(WS_URL, (ws) => {
        connections += 1;
        // backend でセッションが失効した状態。
        mockApi.setCurrentUser(null);
        ws.send(JSON.stringify({ type: "error", error_code: "auth.session_expired", message: "session expired" }));
        ws.close({ code: 1008 });
      });

      await page.goto("/runs");
      await expect(page.getByText("購読を確認する").first()).toBeVisible();
      await useWebSocketMode(page);

      await expect(page).toHaveURL(/\/login$/);
      await expect(page.getByRole("heading", { name: "システムにログイン" })).toBeVisible();
      expect(connections).toBe(1);
    });

    test("コマンド単位の権限エラーは接続を保ったまま理由だけを出す", async ({ page }) => {
      let connections = 0;
      await page.routeWebSocket(WS_URL, (ws) => {
        connections += 1;
        ws.onMessage((message) => {
          const command = JSON.parse(String(message)) as { type: string; command_id: string };
          ws.send(
            JSON.stringify({
              type: "error",
              error_code: "rbac.forbidden",
              message: "cancel requires operator/admin role",
              command: command.type,
              command_id: command.command_id,
            })
          );
        });
      });

      await page.goto("/runs");
      await useWebSocketMode(page);
      await expect(streamCard(page).getByText("接続済み", { exact: true })).toBeVisible();
      await page.getByRole("button", { name: "WS キャンセル" }).click();
      await page.getByRole("alertdialog").getByRole("button", { name: "実行をキャンセル" }).click();

      await expect(streamCard(page).getByText("cancel: この操作を行う権限がありません。")).toBeVisible();
      await expect(streamCard(page).getByText("接続済み", { exact: true })).toBeVisible();
      await expect(page.getByTestId("run-stream-stopped")).toHaveCount(0);
      expect(connections).toBe(1);
    });

    test("SSE の接続が切れたら自動の再接続をやめ、停止を示して再接続できる", async ({ page, mockApi }) => {
      await page.goto("/runs");
      await expect(page.getByText("購読を確認する").first()).toBeVisible();

      // e2e の SSE は空の stream を返して閉じる（fixture）。
      await expect(page.getByTestId("run-stream-stopped")).toContainText("SSE の接続が切れたため");
      const eventRequests = () =>
        mockApi.requests.filter((request) => request.path === "/api/runs/run-stream/events").length;
      const before = eventRequests();
      await page.waitForTimeout(1_500);
      expect(eventRequests()).toBe(before);

      await page.getByRole("button", { name: "再接続" }).click();
      await expect.poll(eventRequests).toBeGreaterThan(before);
    });
  });
}
