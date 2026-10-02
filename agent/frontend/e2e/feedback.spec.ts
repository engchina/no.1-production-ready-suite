import type { Page } from "@playwright/test";

import { MOCK_NOW, expect, test, type MockApi } from "./fixtures/mock-api";

// #774: チャットの回答への評価と、フィードバックの集計の画面。

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

function item(runId: string, overrides: Record<string, unknown> = {}) {
  return {
    run_id: runId,
    thread_id: null,
    agent_id: "default",
    agent_name: "汎用業務 Agent",
    user_uuid: "u-1",
    display_name: "山田 太郎",
    question: `${runId} の質問：今月の契約件数は？`,
    answer: "今月の契約件数は 12 件です。",
    rating: "helpful",
    reason: null,
    comment: "",
    updated_at: MOCK_NOW,
    ...overrides,
  };
}

function seedReport(mockApi: MockApi) {
  mockApi.state.feedbackReport = {
    days: 30,
    since: MOCK_NOW,
    until: MOCK_NOW,
    summary: {
      total: 10,
      helpful: 6,
      not_helpful: 4,
      helpful_rate: 0.6,
      reason_counts: [
        { reason: "incomplete", count: 3 },
        { reason: "wrong_action", count: 1 },
      ],
    },
    previous: { total: 8, helpful: 4, not_helpful: 4, helpful_rate: 0.5, reason_counts: [] },
    items: [
      item("run-a", {
        rating: "not_helpful",
        reason: "incomplete",
        comment: "先月との比較が無い。",
      }),
      item("run-b"),
    ],
    matched: 2,
  };
}

for (const viewport of VIEWPORTS) {
  test(`チャットの回答を評価し、役に立たなかった理由とコメントに付け直せる (${viewport.name})`, async ({ page, mockApi }, testInfo) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await page.goto("/chat");
    const composer = page.getByRole("textbox", { name: "質問" });
    await composer.fill("今月の契約件数は？");
    await composer.press("Enter");
    const conversation = page.getByTestId("chat-conversation");
    await expect(conversation.getByText("「今月の契約件数は？」への回答です。")).toBeVisible();
    const runId = String(mockApi.state.runs.at(-1)?.id ?? mockApi.state.runs[0].id);
    const feedback = page.getByTestId(`chat-feedback-${runId}`);

    // 👍 はすぐ保存する。
    await feedback.getByRole("button", { name: "この回答は役に立った" }).click();
    await expect(page.getByText("フィードバックを保存しました。")).toBeVisible();
    await expect(feedback.getByRole("status")).toHaveText("保存済み・変更できます");
    await expect(feedback.getByRole("button", { name: "この回答は役に立った" })).toHaveAttribute("aria-pressed", "true");
    expect(mockApi.lastRequest("PUT", `/api/runs/${runId}/feedback`)?.body).toEqual({ rating: "helpful" });

    // 👎 は理由（必須）とコメントを開く。理由を選ぶまで保存できない。
    await feedback.getByRole("button", { name: "この回答は役に立たなかった" }).click();
    const save = feedback.getByRole("button", { name: "フィードバックを保存" });
    await expect(save).toBeDisabled();
    await feedback.getByRole("button", { name: "情報が足りない" }).click();
    await feedback.getByLabel("コメント").fill("  先月との比較も欲しい  ");
    await expect(feedback.getByText("14/1000文字")).toBeVisible();
    await feedback.screenshot({ path: testInfo.outputPath(`chat-feedback-${viewport.name}.png`) });
    await save.click();
    await expect(feedback.getByRole("button", { name: "この回答は役に立たなかった" })).toHaveAttribute("aria-pressed", "true");
    expect(mockApi.lastRequest("PUT", `/api/runs/${runId}/feedback`)?.body).toEqual({
      rating: "not_helpful",
      reason: "incomplete",
      comment: "先月との比較も欲しい",
    });
    await expect(save).toHaveCount(0);
    await expectNoHorizontalOverflow(page);
  });

  for (const theme of ["light", "dark"] as const) {
    test(`フィードバックの集計・理由・一覧と詳細を出す (${viewport.name}, ${theme})`, async ({ page, mockApi }, testInfo) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      await useTheme(page, theme);
      seedReport(mockApi);
      await page.goto("/feedback");

      await expect(page.getByRole("heading", { name: "フィードバック", level: 1 })).toBeVisible();
      const summary = page.getByTestId("feedback-summary");
      await expect(summary).toContainText("有効な評価");
      await expect(summary).toContainText("60%");
      await expect(summary).toContainText("前の期間から +10 ポイント");
      await expect(summary).toContainText("前の期間から +2");
      const reasons = page.getByTestId("feedback-reasons");
      await expect(reasons).toContainText("情報が足りない");
      await expect(reasons).toContainText("3 / 75%");

      const table = page.getByRole("table", { name: "フィードバックの一覧" });
      const bad = table.getByRole("row", { name: /run-a/ });
      await expect(bad).toContainText("役に立たなかった");
      await expect(bad).toContainText("情報が足りない");
      await expect(bad).toContainText("山田 太郎");

      await expectNoHorizontalOverflow(page);
      await page.screenshot({ path: testInfo.outputPath(`feedback-${viewport.name}-${theme}.png`), fullPage: true });

      // 行から詳細（質問・回答・理由・コメント）を右の side sheet で開く。
      await bad.getByRole("button", { name: /run-a の質問/ }).click();
      const detail = page.getByRole("dialog", { name: "フィードバックの詳細" });
      await expect(detail).toContainText("今月の契約件数は 12 件です。");
      await expect(detail).toContainText("先月との比較が無い。");
      await expect(detail).toBeInViewport({ ratio: 1 });
      await page.screenshot({ path: testInfo.outputPath(`feedback-detail-${viewport.name}-${theme}.png`) });
      await page.keyboard.press("Escape");
      await expect(detail).toHaveCount(0);
    });
  }
}

test("評価・理由で絞り込み、合う評価が無ければ絞り込みをクリアできる", async ({ page, mockApi }) => {
  seedReport(mockApi);
  await page.goto("/feedback");
  await expect(page.getByRole("table", { name: "フィードバックの一覧" })).toBeVisible();

  // 条件に合う評価が無い応答。
  mockApi.state.feedbackReport = { ...mockApi.state.feedbackReport, items: [], matched: 0 };
  await page.locator("#feedback-rating").click();
  await page.getByRole("option", { name: "役に立たなかった" }).click();
  await expect(page.getByText("条件に合う評価はありません")).toBeVisible();
  expect(mockApi.lastRequest("GET", "/api/feedback")?.searchParams.get("rating")).toBe("not_helpful");

  await page.getByRole("button", { name: "絞り込みをクリア" }).click();
  await expect(page.locator("#feedback-rating")).toContainText("すべて");
  expect(mockApi.lastRequest("GET", "/api/feedback")?.searchParams.get("rating")).toBeNull();
});

test("評価が無い期間は空の状態を出し、読み込み中は経過時間を出す", async ({ page }) => {
  await page.route("**/api/feedback?*", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 1_500));
    await route.fallback();
  });
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("/feedback");
  await expect(page.getByTestId("feedback-loading")).toContainText("フィードバックを読み込んでいます");
  await expect(page.getByText("この期間のフィードバックはありません")).toBeVisible();
  await expectNoHorizontalOverflow(page);
});
