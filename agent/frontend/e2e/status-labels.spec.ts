/**
 * 状態のラベルとページヘッダーの操作を NL2SQL / RAG の基準にそろえたことの確認（#802）。
 *
 * - 状態の enum（running / waiting_approval / pending / read …）を英語のまま出さず、日本語のラベルにする
 *   （design-system ARCHITECTURE §4、lib/status-labels.ts）。
 * - ページの表示更新は `PageHeader` の utility「表示を更新」（buttons.md §5）。
 */
import { expect, MOCK_NOW, test, type MockApi } from "./fixtures/mock-api";

const VIEWPORTS = [
  { name: "desktop", width: 1280, height: 800 },
  { name: "mobile-375", width: 375, height: 812 },
] as const;

const RAW_ENUMS = /^(queued|running|waiting_approval|completed|failed|cancelled|pending|approved|rejected|read|write|sensitive|allow|ask|deny|side_effects)$/;

function seedRun(mockApi: MockApi, id: string, status: string, approvals: Record<string, unknown>[] = []) {
  mockApi.state.runs.unshift({
    id,
    goal: `状態の確認 ${id}`,
    agent_id: "default",
    runtime_id: "builtin",
    status,
    steps: [
      {
        id: `${id}-step`,
        run_id: id,
        kind: "tool",
        status: status === "waiting_approval" ? "waiting_approval" : "completed",
        tool_call: { name: "rag__rag_search", arguments: { query: "契約" } },
      },
    ],
    events: [
      { id: `${id}-event`, run_id: id, type: "tool.completed", message: "ツールを実行しました。", payload: {}, created_at: MOCK_NOW },
    ],
    approvals,
    artifacts: [],
    pending_tool_calls: [],
    metadata: {},
    created_at: MOCK_NOW,
    updated_at: MOCK_NOW,
  });
}

async function expectNoRawEnumBadges(page: import("@playwright/test").Page) {
  const badges = page.locator("main [data-status-variant]");
  await expect(badges.first()).toBeVisible();
  for (const text of await badges.allInnerTexts()) {
    expect(text.trim()).not.toMatch(RAW_ENUMS);
  }
}

for (const viewport of VIEWPORTS) {
  test.describe(`状態のラベル (${viewport.name})`, () => {
    test.use({ viewport: { width: viewport.width, height: viewport.height } });

    test("実行履歴は状態を日本語で出し、表示の更新は utility の「表示を更新」", async ({ page, mockApi }, testInfo) => {
      seedRun(mockApi, "run-status-running", "running");
      seedRun(mockApi, "run-status-waiting", "waiting_approval", [
        {
          id: "approval-status-1",
          run_id: "run-status-waiting",
          step_id: "run-status-waiting-step",
          tool_call: { name: "rag__rag_search", arguments: { query: "契約" } },
          status: "pending",
          reason: "",
          created_at: MOCK_NOW,
        },
      ]);
      await page.goto("/runs");
      const table = page.getByRole("table", { name: "実行履歴" });
      await expect(table.getByTestId("run-row-run-status-running").getByText("実行中", { exact: true })).toBeVisible();
      await expect(table.getByTestId("run-row-run-status-waiting").getByText("承認待ち", { exact: true })).toBeVisible();
      // 目標の下の補足は業務 Agent の ID ではなく名前。
      await expect(table.getByTestId("run-row-run-status-running")).toContainText("汎用業務 Agent");
      await expectNoRawEnumBadges(page);

      const refresh = page.getByRole("button", { name: "表示を更新" });
      await expect(refresh).toBeVisible();
      const before = mockApi.requests.filter((request) => request.method === "GET" && request.path === "/api/runs").length;
      await refresh.click();
      await expect
        .poll(() => mockApi.requests.filter((request) => request.method === "GET" && request.path === "/api/runs").length)
        .toBeGreaterThan(before);
      await page.screenshot({ path: testInfo.outputPath(`runs-status-${viewport.name}.png`), fullPage: true });

      await page.goto("/approvals");
      await expect(page.getByRole("region", { name: "承認の詳細" }).getByText("保留中", { exact: true })).toBeVisible();
      await expect(page.getByRole("region", { name: "承認の詳細" })).toContainText("実行の状態: 承認待ち");
      await expectNoRawEnumBadges(page);
    });

    test("監査ログの絞り込みの選択肢と、ツール・ツール権限の権限を日本語で出す", async ({ page }) => {
      await page.goto("/audit");
      await expect(page.getByRole("button", { name: "表示を更新" })).toBeVisible();
      await page.locator("#audit-step-status").click();
      await expect(page.getByRole("option", { name: "承認待ち" })).toBeVisible();
      await expect(page.getByRole("option", { name: "waiting_approval" })).toHaveCount(0);
      await page.keyboard.press("Escape");
      await page.locator("#audit-approval-status").click();
      await expect(page.getByRole("option", { name: "却下済み" })).toBeVisible();
      await page.keyboard.press("Escape");

      await page.goto("/settings/tool-policy");
      await expectNoRawEnumBadges(page);

      await page.goto("/settings/runtime-snapshot");
      await expect(page.getByRole("button", { name: "表示を更新" })).toBeVisible();
      const summary = page.getByLabel("サマリー");
      await expect(summary.getByText("保留中のツール呼び出し")).toBeVisible();
      await expect(summary.getByText("pending_tool_calls")).toHaveCount(0);
    });
  });
}
