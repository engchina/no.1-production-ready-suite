import type { Page } from "@playwright/test";

import { MOCK_NOW, emptyUsageReport, expect, test, type MockApi } from "./fixtures/mock-api";

// #772: 利用状況（Run のモデル利用量の集計）と、Run の詳細の利用量。

const VIEWPORTS = [
  { name: "desktop", width: 1280, height: 900 },
  { name: "mobile-375", width: 375, height: 812 },
];

async function expectNoHorizontalOverflow(page: Page) {
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth
  );
  expect(overflow).toBeLessThanOrEqual(0);
}

async function useTheme(page: Page, theme: "light" | "dark") {
  await page.addInitScript((value) => {
    window.localStorage.setItem("production-ready-agent.ui", JSON.stringify({ state: { theme: value }, version: 0 }));
  }, theme);
}

function totals(runs: number, input: number, output: number, recorded = runs) {
  return {
    runs,
    runs_with_usage: recorded,
    requests: recorded * 2,
    input_tokens: input,
    output_tokens: output,
    total_tokens: input + output,
  };
}

function seedReport(mockApi: MockApi) {
  const report = emptyUsageReport(30) as Record<string, unknown> & { by_day: Record<string, unknown>[] };
  report.totals = totals(12, 90_000, 10_000, 11);
  report.previous = totals(8, 60_000, 5_000);
  report.by_agent = [
    { agent_id: "sales", agent_name: "営業の Agent", ...totals(7, 70_000, 5_000) },
    { agent_id: "default", agent_name: "汎用業務 Agent", ...totals(4, 20_000, 5_000) },
    { agent_id: "removed-agent", agent_name: "", ...totals(1, 0, 0, 0) },
  ];
  report.by_user = [
    { user_uuid: "u-1", display_name: "山田 太郎", ...totals(9, 80_000, 8_000) },
    { user_uuid: null, display_name: "", ...totals(3, 10_000, 2_000) },
  ];
  report.by_model = [
    { model: "xai.grok-4", ...totals(11, 90_000, 10_000) },
    { model: "", ...totals(1, 0, 0, 0) },
  ];
  const last = report.by_day[report.by_day.length - 1];
  report.by_day[report.by_day.length - 1] = { ...last, ...totals(5, 40_000, 4_000) };
  mockApi.state.usageReports["30"] = report;
}

for (const viewport of VIEWPORTS) {
  for (const theme of ["light", "dark"] as const) {
    test(`期間の合計と前の期間との差、内訳を出す (${viewport.name}, ${theme})`, async ({ page, mockApi }, testInfo) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      await useTheme(page, theme);
      seedReport(mockApi);
      await page.goto("/usage");

      await expect(page.getByRole("heading", { name: "利用状況", level: 1 })).toBeVisible();
      const summary = page.getByTestId("usage-summary");
      await expect(summary).toContainText("合計 token");
      await expect(summary).toContainText("100,000");
      await expect(summary).toContainText("前の期間から +35,000");
      // Run あたりの token は、利用量を記録した Run（11 件）で割る（100,000 / 11 ≒ 9,091。前は 65,000 / 8 ≒ 8,125）。
      await expect(summary).toContainText("9,091");
      await expect(summary).toContainText("前の期間から +966");
      await expect(page.getByTestId("usage-unrecorded")).toContainText("利用量の記録がない Run が 1 件あります");

      // 業務 Agent ごと（既定のタブ）。token の割合を数字でも出す。
      const agents = page.getByRole("table", { name: "業務 Agent ごとの利用量" });
      const sales = agents.getByRole("row", { name: /営業の Agent/ });
      await expect(sales).toContainText("75,000");
      await expect(sales).toContainText("75%");
      await expect(agents.getByRole("row", { name: /removed-agent/ })).toContainText("削除した業務 Agent");

      // 利用者ごと・モデルごとはタブで切り替える（← → でも移れる）。
      await page.getByRole("tab", { name: /利用者ごと/ }).click();
      const users = page.getByRole("table", { name: "利用者ごとの利用量" });
      await expect(users.getByRole("row", { name: /山田 太郎/ })).toContainText("88,000");
      await expect(users).toContainText("作成者の記録なし");
      await page.getByRole("tab", { name: /利用者ごと/ }).press("ArrowRight");
      const models = page.getByRole("table", { name: "モデルごとの利用量" });
      await expect(models).toContainText("xai.grok-4");
      await expect(models).toContainText("記録なし");

      // 日ごとは新しい日から。期間の 30 日をページングする。
      await page.getByRole("tab", { name: "日ごと" }).click();
      const days = page.getByRole("table", { name: "日ごとの利用量" });
      await expect(days.getByRole("row").nth(1)).toContainText("44,000");
      await expect(page.getByText("1 - 10 / 30 件")).toBeVisible();

      await expectNoHorizontalOverflow(page);
      await page.screenshot({ path: testInfo.outputPath(`usage-${viewport.name}-${theme}.png`), fullPage: true });
    });
  }
}

test("期間を変えると、その期間で集計し直し、Run が無ければ空の状態を出す", async ({ page, mockApi }) => {
  seedReport(mockApi);
  await page.goto("/usage");
  await expect(page.getByTestId("usage-summary")).toContainText("100,000");

  await page.locator("#usage-period").click();
  await page.getByRole("option", { name: "直近 7 日" }).click();

  await expect(page.getByText("この期間の Run はありません")).toBeVisible();
  const request = mockApi.lastRequest("GET", "/api/usage");
  expect(request?.searchParams.get("days")).toBe("7");
  // 日は画面のブラウザのタイムゾーンで区切る。
  expect(request?.searchParams.get("timezone")).toBeTruthy();
  // 期間は作業状態に残す（再読込しても 7 日）。
  await page.reload();
  await expect(page.locator("#usage-period")).toContainText("直近 7 日");
});

test("90 日を超える期間（180・365 日）を選べ、集計元を出す", async ({ page, mockApi }) => {
  // #794: Oracle の構成は保存した Run の履歴（AGENT_RUN_FACTS）を集計する。
  const report = emptyUsageReport(365) as Record<string, unknown>;
  report.source = "history";
  report.totals = totals(3, 3_000, 300);
  mockApi.state.usageReports["365"] = report;
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("/usage");
  await expect(page.getByTestId("report-source")).toHaveAttribute("data-source", "memory");

  await page.locator("#usage-period").click();
  await expect(page.getByRole("option", { name: "直近 180 日" })).toBeVisible();
  await page.getByRole("option", { name: "直近 365 日" }).click();

  await expect(page.getByTestId("usage-summary")).toContainText("3,300");
  await expect(page.getByTestId("report-source")).toHaveText("保存した Run の履歴（データベース）から集計しています。");
  expect(mockApi.lastRequest("GET", "/api/usage")?.searchParams.get("days")).toBe("365");
  await page.getByRole("tab", { name: "日ごと" }).click();
  await expect(page.getByText("1 - 10 / 365 件")).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

test("読み込み中は経過時間と集計の形の Skeleton を出す", async ({ page }) => {
  await page.route("**/api/usage?*", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 1_500));
    await route.fallback();
  });
  await page.goto("/usage");
  await expect(page.getByTestId("usage-loading")).toContainText("利用状況を読み込んでいます");
  await expect(page.getByText("この期間の Run はありません")).toBeVisible();
});

test("Run の詳細にモデルの利用量を出す", async ({ page, mockApi }) => {
  mockApi.state.runs.push({
    id: "run-usage-772",
    goal: "今月の売上は？",
    agent_id: "default",
    runtime_id: "builtin",
    status: "completed",
    steps: [],
    events: [],
    approvals: [],
    artifacts: [],
    pending_tool_calls: [],
    metadata: {},
    usage: { model: "xai.grok-4", requests: 2, input_tokens: 1200, output_tokens: 300, total_tokens: 1500 },
    created_at: MOCK_NOW,
    updated_at: MOCK_NOW,
  });
  await page.goto("/runs");
  await expect(page.getByTestId("run-usage")).toHaveText(
    "モデルの利用量: xai.grok-4 を 2 回呼び出し、入力 1,200 / 出力 300 token（合計 1,500）"
  );
});
