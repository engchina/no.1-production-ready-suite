import type { Page } from "@playwright/test";

import { MOCK_NOW, evaluationSummary, expect, test, type MockApi } from "./fixtures/mock-api";

// #776: 業務 Agent の品質評価（評価ケースで実行し、LLM で判定して合格率を出す）。

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

function seedFinishedJob(mockApi: MockApi) {
  const results = [
    {
      case: { id: "expense-deadline", question: "経費精算の締め日はいつですか？", expected: "毎月 25 日" },
      status: "judged",
      run_id: "run-eval-1",
      answer: "毎月 25 日です。",
      judgement: { verdict: "correct", score: 1, summary: "要点を満たしています。", missing_points: [] },
      error: null,
      duration_ms: 3200,
    },
    {
      case: { id: "sales-total", question: "今月の売上の合計はいくらですか？", expected: "合計金額（円）" },
      status: "judged",
      run_id: "run-eval-2",
      answer: "分かりません。",
      judgement: {
        verdict: "incorrect",
        score: 0.2,
        summary: "金額を答えていません。",
        missing_points: ["今月の売上の合計金額"],
      },
      error: null,
      duration_ms: 5100,
    },
    {
      case: { id: "register", question: "取引先を登録して", expected: "登録した" },
      status: "needs_approval",
      run_id: "run-eval-3",
      answer: "",
      judgement: null,
      error: "承認が必要なツールを使うため評価できません（Run は取り消しました）。",
      duration_ms: 2100,
    },
  ];
  mockApi.state.evaluations.push({
    id: "eval-seeded",
    agent_id: "default",
    agent_name: "汎用業務 Agent",
    status: "completed",
    created_by_user_uuid: "local",
    results,
    error: null,
    summary: evaluationSummary(results),
    created_at: MOCK_NOW,
    started_at: MOCK_NOW,
    finished_at: MOCK_NOW,
  });
}

for (const viewport of VIEWPORTS) {
  test(`評価ケースで評価を始め、進み具合の後に概要とケース別結果を出す (${viewport.name})`, async ({ page, mockApi }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await page.goto("/evaluation");
    await expect(page.getByRole("heading", { name: "品質評価", level: 1 })).toBeVisible();
    await expect(page.getByText("まだ評価はありません")).toBeVisible();

    // JSON の誤りはその場で知らせ、開始しない。
    const cases = page.getByLabel("評価ケース（JSON）");
    await cases.fill("{ 壊れた JSON");
    await expect(page.getByText("JSON の形式が正しくありません。")).toBeVisible();
    await page.getByTestId("evaluation-start").click();
    expect(mockApi.lastRequest("POST", "/api/evaluations")).toBeUndefined();

    await page.getByRole("button", { name: "サンプルを読み込む" }).click();
    await expect(page.getByText("2 件のケース")).toBeVisible();
    await page.getByTestId("evaluation-start").click();
    await expect(page.getByText("評価を開始しました")).toBeVisible();
    const body = mockApi.lastRequest("POST", "/api/evaluations")?.body as { agent_id: string; cases: unknown[] };
    expect(body.agent_id).toBe("default");
    expect(body.cases).toHaveLength(2);

    // 実行中は進み具合を出し、終わったら概要とケース別結果。
    await expect(page.getByTestId("evaluation-progress")).toContainText("汎用業務 Agent を評価しています（0 / 2 件）");
    const summary = page.getByTestId("evaluation-summary");
    await expect(summary).toContainText("50%");
    await expect(page.getByTestId("evaluation-progress")).toHaveCount(0);
    const results = page.getByRole("table", { name: "ケース別結果" });
    await expect(results.getByRole("row", { name: /expense-deadline/ })).toContainText("正しい");
    await expect(results.getByRole("row", { name: /sales-total/ })).toContainText("誤り");
    await expect(page.getByRole("table", { name: "最近の評価" })).toContainText("完了");
    await expectNoHorizontalOverflow(page);
  });

  for (const theme of ["light", "dark"] as const) {
    test(`評価の概要・ケース別結果・詳細を出す (${viewport.name}, ${theme})`, async ({ page, mockApi }, testInfo) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      await useTheme(page, theme);
      seedFinishedJob(mockApi);
      await page.goto("/evaluation");

      const summary = page.getByTestId("evaluation-summary");
      await expect(summary).toContainText("合格率");
      await expect(summary).toContainText("33%");
      await expect(summary).toContainText("0.60");
      await expect(summary).toContainText("評価できなかった");
      const results = page.getByRole("table", { name: "ケース別結果" });
      await expect(results.getByRole("row", { name: /register/ })).toContainText("承認が必要");
      await expectNoHorizontalOverflow(page);
      await page.screenshot({ path: testInfo.outputPath(`evaluation-${viewport.name}-${theme}.png`), fullPage: true });

      await results.getByRole("button", { name: /今月の売上の合計はいくらですか？/ }).click();
      const detail = page.getByRole("dialog", { name: "ケースの詳細" });
      await expect(detail).toContainText("合計金額（円）");
      await expect(detail).toContainText("分かりません。");
      await expect(detail).toContainText("金額を答えていません。");
      await expect(detail).toContainText("今月の売上の合計金額");
      await expect(detail).toBeInViewport({ ratio: 1 });
      await page.screenshot({ path: testInfo.outputPath(`evaluation-detail-${viewport.name}-${theme}.png`) });
    });
  }
}

test("実行中の評価は取り消せ、終わった評価は確認してから削除できる", async ({ page, mockApi }) => {
  mockApi.state.evaluationPollsUntilDone = 1_000;
  seedFinishedJob(mockApi);
  await page.goto("/evaluation");
  await page.getByRole("button", { name: "サンプルを読み込む" }).click();
  await page.getByTestId("evaluation-start").click();
  const progress = page.getByTestId("evaluation-progress");
  await expect(progress).toBeVisible();
  // 実行中はほかの評価を始められない。
  await expect(page.getByTestId("evaluation-start")).toBeDisabled();

  await progress.getByRole("button", { name: "取り消し" }).click();
  await expect(page.getByText("評価を取り消しました")).toBeVisible();
  await expect(progress).toHaveCount(0);
  expect(mockApi.lastRequest("POST", "/api/evaluations/eval-2/cancel")).toBeDefined();

  // 前の評価を表示してから削除する（確認のダイアログ）。
  await page.getByTestId("evaluation-row-actions-eval-seeded").click();
  await page.getByRole("menuitem", { name: "削除" }).click();
  const dialog = page.getByRole("alertdialog").or(page.getByRole("dialog"));
  await expect(dialog.getByText("この評価を削除しますか?")).toBeVisible();
  await dialog.getByRole("button", { name: "削除", exact: true }).click();
  await expect(page.getByText("評価を削除しました")).toBeVisible();
  expect(mockApi.lastRequest("DELETE", "/api/evaluations/eval-seeded")).toBeDefined();
});

test("読み込み中は経過時間を出す", async ({ page, mockApi }) => {
  seedFinishedJob(mockApi);
  await page.route("**/api/evaluations/eval-seeded", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 1_500));
    await route.fallback();
  });
  await page.goto("/evaluation");
  await expect(page.getByTestId("evaluation-loading")).toContainText("評価の結果を読み込んでいます");
  await expect(page.getByTestId("evaluation-summary")).toBeVisible();
});
