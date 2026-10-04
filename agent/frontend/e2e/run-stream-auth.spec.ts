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
    .filter({ hasText: "実行のイベントの受け取り方" })
    .last();
}

async function openStreamControls(page: Page) {
  await page.getByRole("tab", { name: "実行の経過", exact: true }).click();
  await page.locator("summary").filter({ hasText: /^ストリーム/ }).click();
}

async function useWebSocketMode(page: Page) {
  await openStreamControls(page);
  await page.getByRole("group", { name: "ストリーム方式" }).getByRole("button", { name: "WebSocket" }).click();
}

/** Run の詳細の操作（直置きのボタン、無ければ「その他の操作」のメニュー）を押す。 */
async function runObjectAction(page: Page, name: string) {
  const bar = page.getByTestId("run-object-actions");
  const direct = bar.getByRole("button", { name, exact: true });
  if (await direct.count()) {
    await direct.click();
    return;
  }
  await bar.getByRole("button", { name: /その他の操作/ }).click();
  await page.getByRole("menuitem", { name, exact: true }).click();
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
        ws.send(JSON.stringify({ type: "error", error_code: "rbac.agent_forbidden", message: "この業務 Agent の実行を参照する権限がありません。" }));
        ws.close({ code: 1008, reason: "forbidden" });
      });

      await page.goto("/runs?id=run-stream");
      await expect(page.getByText("購読を確認する").first()).toBeVisible();
      await useWebSocketMode(page);
      // 選んだ方式の説明は常設せず、チップの横の info アイコンから出す（読み上げには結び付く。#901）。
      const streamInfo = page.getByRole("button", { name: "ストリーム方式の説明", exact: true });
      await expect(streamInfo).toHaveAccessibleDescription(/WebSocket/);
      await expect(page.getByTestId("run-stream-description")).toBeHidden();
      await streamInfo.click();
      await expect(page.getByTestId("run-stream-description")).toBeVisible();
      await page.keyboard.press("Escape");
      await expect(page.getByTestId("run-stream-description")).toBeHidden();

      await expect(page.getByTestId("run-stream-stopped")).toHaveText("この実行のイベントを購読する権限がありません。");
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

      await page.goto("/runs?id=run-stream");
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
              message: "実行を取り消す権限がありません。",
              command: command.type,
              command_id: command.command_id,
            })
          );
        });
      });

      await page.goto("/runs?id=run-stream");
      await useWebSocketMode(page);
      await expect(streamCard(page).getByText("接続済み", { exact: true })).toBeVisible();
      // 操作は Run の詳細の ObjectActionBar の 1 か所。購読中は WebSocket で送る（#814）。
      await runObjectAction(page, "キャンセル");
      await page.getByRole("alertdialog").getByRole("button", { name: "実行をキャンセル" }).click();

      // 拒否は REST の操作の失敗と同じく danger の Toast で返し、接続は保つ。
      await expect(page.getByText("実行の操作を送れませんでした")).toBeVisible();
      await expect(page.getByText("この操作を行う権限がありません。")).toBeVisible();
      await expect(streamCard(page).getByText("接続済み", { exact: true })).toBeVisible();
      await expect(page.getByTestId("run-stream-stopped")).toHaveCount(0);
      expect(connections).toBe(1);
    });

    test("権限以外のコマンドの拒否は backend の日本語の文を出し、文が無ければ error code ではなく既定の文を出す", async ({
      page,
    }) => {
      // backend の `_handle_websocket_command` と同じ形の拒否（#1031）。1 回目は文あり、2 回目は文なし。
      const replies = [
        { error_code: "run.not_found", message: "実行が見つかりません。" },
        { error_code: "websocket.command_id_conflict" },
      ];
      await page.routeWebSocket(WS_URL, (ws) => {
        ws.onMessage((message) => {
          const command = JSON.parse(String(message)) as { type: string; command_id: string };
          const reply = replies.shift();
          ws.send(JSON.stringify({ type: "error", ...reply, command: command.type, command_id: command.command_id }));
        });
      });

      await page.goto("/runs?id=run-stream");
      await useWebSocketMode(page);
      await expect(streamCard(page).getByText("接続済み", { exact: true })).toBeVisible();
      const resume = page.getByTestId("run-object-actions").getByRole("button", { name: "再開", exact: true });

      await resume.click();
      const toasts = page.locator("[data-toast-placement]");
      await expect(toasts).toContainText("実行の操作を送れませんでした");
      await expect(toasts).toContainText("実行が見つかりません。");
      await expect(resume).not.toHaveAttribute("aria-busy", "true");

      await resume.click();
      await expect(toasts).toContainText("サーバーが操作を受け付けませんでした。");
      // error code（技術情報）は Toast に出さない。
      await expect(toasts).not.toContainText("websocket.command_id_conflict");
      await expect(streamCard(page).getByText("接続済み", { exact: true })).toBeVisible();
      await expectNoPageOverflow(page);
    });

    test("WebSocket の購読中は Run の操作をその接続で送り、押した操作だけが受付まで処理中になる", async ({
      page,
      mockApi,
    }) => {
      const commands: Array<{ type: string; command_id: string }> = [];
      let socket: WebSocketRoute | null = null;
      await page.routeWebSocket(WS_URL, (ws) => {
        socket = ws;
        ws.onMessage((message) => {
          commands.push(JSON.parse(String(message)) as { type: string; command_id: string });
        });
      });

      await page.goto("/runs?id=run-stream");
      await useWebSocketMode(page);
      await expect(streamCard(page).getByText("接続済み", { exact: true })).toBeVisible();
      // WebSocket 専用のボタン列は無く、操作は ObjectActionBar の 1 か所だけ。
      await expect(streamCard(page).getByRole("button", { name: /WS / })).toHaveCount(0);
      // 通信の指標は「接続の詳細」に畳む（既定で閉じる）。
      const details = page.getByTestId("run-stream-details");
      await expect(details).not.toHaveAttribute("open", "");
      await expect(details.getByText("最後の応答確認")).toBeHidden();

      const bar = page.getByTestId("run-object-actions");
      const resume = bar.getByRole("button", { name: "再開", exact: true });
      await resume.click();
      await expect.poll(() => commands.map((command) => command.type)).toEqual(["resume"]);
      expect(mockApi.lastRequest("POST", "/api/runs/run-stream/resume")).toBeUndefined();
      // 押した「再開」だけが処理中（スピナーは 1 つ）、他の操作は押せない。
      await expect(resume).toHaveAttribute("aria-busy", "true");
      await expect(bar.getByRole("button", { name: "再実行", exact: true })).toBeDisabled();
      // 操作の列で回るのは押した「再開」だけ（実行中の Run の進行表示のスピナーは別の処理）。
      await expect(resume.locator("svg.animate-spin")).toHaveCount(1);
      await expect(bar.locator("svg.animate-spin:visible")).toHaveCount(1);

      socket!.send(JSON.stringify({ type: "command.accepted", command: "resume", command_id: commands[0].command_id }));
      await expect(resume).not.toHaveAttribute("aria-busy", "true");
      await details.getByText("接続の詳細").click();
      await expect(details.getByText(/^resume \/ resume-/)).toBeVisible();
      await expectNoPageOverflow(page);
    });

    test("SSE の購読中は Run の操作を REST で送る", async ({ page, mockApi }) => {
      await page.goto("/runs?id=run-stream");
      await expect(page.getByText("購読を確認する").first()).toBeVisible();
      await page.getByTestId("run-object-actions").getByRole("button", { name: "再開", exact: true }).click();
      await expect.poll(() => mockApi.lastRequest("POST", "/api/runs/run-stream/resume")).toBeTruthy();
    });

    test("SSE の接続が切れたら自動の再接続をやめ、停止を示して再接続できる", async ({ page, mockApi }) => {
      await page.goto("/runs?id=run-stream");
      await expect(page.getByText("購読を確認する").first()).toBeVisible();

      await openStreamControls(page);
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
