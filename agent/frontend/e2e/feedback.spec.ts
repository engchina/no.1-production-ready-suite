import type { Page } from "@playwright/test";

import { dbUser } from "./fixtures/auth";
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
    feedback: rating("helpful"),
    admin_review: null,
    reviewer_display_name: "",
    updated_at: MOCK_NOW,
    ...overrides,
  };
}

function rating(value: "helpful" | "not_helpful", reason: string | null = null, comment = "") {
  return { rating: value, reason, comment, user_uuid: "u-1", updated_at: MOCK_NOW };
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
      admin_reviewed: 3,
      admin_not_helpful: 1,
    },
    previous: {
      total: 8,
      helpful: 4,
      not_helpful: 4,
      helpful_rate: 0.5,
      reason_counts: [],
      admin_reviewed: 0,
      admin_not_helpful: 0,
    },
    items: [
      item("run-a", { feedback: rating("not_helpful", "incomplete", "先月との比較が無い。") }),
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
      await expect(page.getByTestId("feedback-admin-summary")).toHaveText(
        "管理者の評価: 3 件（うち役に立たなかった 1 件）"
      )

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

for (const viewport of VIEWPORTS) {
  test(`365 日までの期間を選び、一覧はサーバー側でページングする (${viewport.name})`, async ({ page, mockApi }) => {
    // #794: 90 日を超える期間は保存した Run の履歴で集計し、一覧は offset / limit で取得する。
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    seedReport(mockApi);
    const report = mockApi.state.feedbackReport as Record<string, unknown>;
    report.source = "history";
    report.items = Array.from({ length: 23 }, (_, index) => item(`run-${String(index).padStart(2, "0")}`));
    report.matched = 23;
    await page.goto("/feedback");

    await expect(page.getByTestId("report-source")).toHaveText("保存した実行の履歴（データベース）から集計しています。");
    const table = page.getByRole("table", { name: "フィードバックの一覧" });
    await expect(table.getByRole("row", { name: /run-00/ })).toBeVisible();
    const pager = page.getByTestId("feedback-pagination");
    await expect(pager).toContainText("1 - 10 / 23 件");
    await pager.getByRole("button", { name: "次へ" }).click();
    await expect(table.getByRole("row", { name: /run-10/ })).toBeVisible();
    await expect(pager).toContainText("11 - 20 / 23 件");
    let request = mockApi.lastRequest("GET", "/api/feedback");
    expect([request?.searchParams.get("offset"), request?.searchParams.get("limit")]).toEqual(["10", "10"]);

    // ページは作業状態に残す（再読込しても 2 ページ目）。
    await page.reload();
    await expect(page.getByTestId("feedback-pagination")).toContainText("11 - 20 / 23 件");

    // 期間を変えると 1 ページ目から読み直す。
    await page.locator("#feedback-period").click();
    await page.getByRole("option", { name: "直近 365 日" }).click();
    await expect(page.getByTestId("feedback-pagination")).toContainText("1 - 10 / 23 件");
    request = mockApi.lastRequest("GET", "/api/feedback");
    expect(request?.searchParams.get("days")).toBe("365");
    expect(request?.searchParams.get("offset")).toBeNull();
    await expectNoHorizontalOverflow(page);
  });
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

function seedRatedRun(mockApi: MockApi) {
  mockApi.state.runs.push({
    id: "run-admin-774",
    goal: "今月の契約件数は？",
    agent_id: "default",
    runtime_id: "builtin",
    status: "completed",
    steps: [],
    events: [],
    approvals: [],
    artifacts: [
      { id: "answer-1", name: "回答", kind: "answer", content: { text: "今月は 12 件です。" }, created_at: MOCK_NOW },
    ],
    pending_tool_calls: [],
    metadata: {},
    created_by_user_uuid: "u-2",
    feedback: rating("helpful"),
    created_at: MOCK_NOW,
    updated_at: MOCK_NOW,
  });
}

test("管理者はだれの回答にも評価を付けられ、本人の評価とは別に残る", async ({ page, mockApi }) => {
  seedRatedRun(mockApi);
  await page.goto("/feedback");
  await page
    .getByRole("table", { name: "フィードバックの一覧" })
    .getByRole("button", { name: /今月の契約件数は？/ })
    .click();
  const detail = page.getByRole("dialog", { name: "フィードバックの詳細" });
  await expect(detail.getByTestId("feedback-detail-owner")).toContainText("役に立った");
  await expect(detail.getByTestId("feedback-detail-admin")).toContainText("まだ評価していません。");

  const review = detail.getByTestId("admin-review-run-admin-774");
  await expect(review).toContainText("管理者の評価");
  await review.getByRole("button", { name: "この回答は役に立たなかった" }).click();
  await review.getByRole("button", { name: "内容が正しくない" }).click();
  await review.getByLabel("コメント").fill("件数は 15 件が正しい");
  await review.getByRole("button", { name: "フィードバックを保存" }).click();
  await expect(page.getByText("フィードバックを保存しました。")).toBeVisible();
  expect(mockApi.lastRequest("PUT", "/api/runs/run-admin-774/admin-review")?.body).toEqual({
    rating: "not_helpful",
    reason: "incorrect",
    comment: "件数は 15 件が正しい",
  });
  await expect(detail.getByTestId("feedback-detail-admin")).toContainText("内容が正しくない");
  // 本人の評価はそのまま。
  await expect(detail.getByTestId("feedback-detail-owner")).toContainText("役に立った");
  expect(mockApi.lastRequest("PUT", "/api/runs/run-admin-774/feedback")).toBeUndefined();

  // Run の詳細でも管理者の評価を付け直せる（付けた評価が選ばれている）。
  await page.keyboard.press("Escape");
  await page.goto("/runs?id=run-admin-774");
  const runReview = page.getByTestId("admin-review-run-admin-774");
  await expect(runReview.getByRole("button", { name: "この回答は役に立たなかった" })).toHaveAttribute(
    "aria-pressed",
    "true"
  );
});

test("Agent 管理の権限が無い利用者には管理者の評価の操作を出さない", async ({ page, mockApi }) => {
  seedRatedRun(mockApi);
  mockApi.setCurrentUser(dbUser({ permissions: ["menu.feedback"] }));
  await page.goto("/feedback");
  await page
    .getByRole("table", { name: "フィードバックの一覧" })
    .getByRole("button", { name: /今月の契約件数は？/ })
    .click();
  const detail = page.getByRole("dialog", { name: "フィードバックの詳細" });
  await expect(detail.getByTestId("feedback-detail-admin")).toContainText("まだ評価していません。");
  await expect(detail.getByTestId("admin-review-run-admin-774")).toHaveCount(0);
});

// ---- #810: 役に立たなかった回答を評価ケースに追加する -------------------------------------------------

const DISLIKED_QUESTION = "先月の契約件数は？";

function seedDislikedRun(mockApi: MockApi, { comment = "件数は 15 件が正しい" }: { comment?: string } = {}) {
  mockApi.state.runs.push({
    id: "run-dislike-810",
    goal: DISLIKED_QUESTION,
    agent_id: "default",
    runtime_id: "builtin",
    status: "completed",
    steps: [
      { id: "step-1", run_id: "run-dislike-810", kind: "tool", status: "completed", tool_call: { name: "nl2sql__nl2sql_query", arguments: {} } },
      { id: "step-2", run_id: "run-dislike-810", kind: "tool", status: "completed", tool_call: { name: "rag__rag_search", arguments: {} } },
    ],
    events: [],
    approvals: [],
    artifacts: [
      { id: "answer-810", name: "回答", kind: "answer", content: { text: "先月は 12 件です。" }, created_at: MOCK_NOW },
    ],
    pending_tool_calls: [],
    metadata: {},
    created_by_user_uuid: "u-2",
    feedback: rating("not_helpful", "incorrect", "件数が違う"),
    admin_review: comment ? rating("not_helpful", "incorrect", comment) : null,
    created_at: MOCK_NOW,
    updated_at: MOCK_NOW,
  });
}

function seedEvaluationSets(mockApi: MockApi) {
  const base = { agent_id: "default", description: "", created_by_user_uuid: "local", created_at: MOCK_NOW, updated_at: MOCK_NOW };
  mockApi.state.evaluationSets.push(
    {
      ...base,
      id: "evset-dup",
      name: "契約の評価",
      cases: [{ id: "case-1", question: DISLIKED_QUESTION, expected: "15 件", expected_tools: [], source_run_id: null }],
    },
    {
      ...base,
      id: "evset-open",
      name: "月次の評価",
      cases: [{ id: "case-1", question: "今月の売上は？", expected: "300 万円", expected_tools: [], source_run_id: null }],
    }
  );
}

async function openDislikedDetail(page: Page) {
  await page.goto("/feedback");
  await page
    .getByRole("table", { name: "フィードバックの一覧" })
    .getByRole("button", { name: new RegExp(DISLIKED_QUESTION) })
    .click();
  return page.getByRole("dialog", { name: "フィードバックの詳細" });
}

for (const viewport of VIEWPORTS) {
  for (const theme of ["light", "dark"] as const) {
    test(`役に立たなかった回答を既存の評価セットに追加する (${viewport.name}, ${theme})`, async ({ page, mockApi }, testInfo) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      await useTheme(page, theme);
      seedDislikedRun(mockApi);
      seedEvaluationSets(mockApi);
      const detail = await openDislikedDetail(page);

      await detail.getByTestId("feedback-add-case-toggle").click();
      const form = detail.getByTestId("feedback-add-case-form");
      // 質問・管理者のコメント・呼んだツールが入り、同じ質問を持たない評価セットが既定の追加先。
      await expect(form.getByLabel("質問")).toHaveValue(DISLIKED_QUESTION);
      await expect(form.getByLabel("期待する回答の要点")).toHaveValue("件数は 15 件が正しい");
      await expect(form.getByLabel("期待するツール")).toHaveValue("nl2sql__nl2sql_query, rag__rag_search");
      const set = form.getByTestId("feedback-add-case-set");
      await expect(set).toContainText("月次の評価（1 件）");

      // 同じ質問を持つ評価セットを選ぶと、その場で知らせて追加しない。
      await set.click();
      await page.getByRole("option", { name: "契約の評価（1 件）" }).click();
      await expect(form.getByText("同じ質問のケースが既にあります")).toBeVisible();
      await form.getByRole("button", { name: "評価ケースに追加" }).click();
      expect(mockApi.lastRequest("POST", "/api/evaluation-sets/evset-dup/cases")).toBeUndefined();

      await form.getByLabel("期待するツール").fill("nl2sql_query");
      await set.click();
      await page.getByRole("option", { name: "月次の評価（1 件）" }).click();
      await expect(set).toHaveAttribute("aria-invalid", "false");
      await expect(form.getByText("同じ質問のケースが既にあります")).toHaveCount(0);
      await expect(detail).toBeInViewport({ ratio: 1 });
      await expectNoHorizontalOverflow(page);
      await page.screenshot({ path: testInfo.outputPath(`feedback-add-case-${viewport.name}-${theme}.png`) });
      await form.getByRole("button", { name: "評価ケースに追加" }).click();

      await expect(page.getByText("「月次の評価」に評価ケースを追加しました")).toBeVisible();
      expect(mockApi.lastRequest("POST", "/api/evaluation-sets/evset-open/cases")?.body).toEqual({
        question: DISLIKED_QUESTION,
        expected: "件数は 15 件が正しい",
        expected_tools: ["nl2sql_query"],
        source_run_id: "run-dislike-810",
      });
      // 追加したら閉じる。
      await expect(detail.getByTestId("feedback-add-case-form")).toHaveCount(0);
    });
  }
}

test("評価セットが無ければ新しい評価セットを作り、期待する回答の要点は必須にする", async ({ page, mockApi }) => {
  seedDislikedRun(mockApi, { comment: "" });
  const detail = await openDislikedDetail(page);
  await detail.getByTestId("feedback-add-case-toggle").click();
  const form = detail.getByTestId("feedback-add-case-form");
  await expect(form.getByTestId("feedback-add-case-set")).toContainText("新しい評価セットを作る");
  await expect(form.getByLabel("新しい評価セットの名前")).toHaveValue("汎用業務 Agent のフィードバック");
  await expect(form.getByLabel("期待する回答の要点")).toHaveValue("");

  await form.getByRole("button", { name: "評価ケースに追加" }).click();
  await expect(form.getByText("期待する回答の要点を入力してください。")).toBeVisible();
  await expect(form.getByLabel("期待する回答の要点")).toBeFocused();
  expect(mockApi.lastRequest("POST", "/api/evaluation-sets")).toBeUndefined();

  await form.getByLabel("期待する回答の要点").fill("15 件");
  await form.getByRole("button", { name: "評価ケースに追加" }).click();
  await expect(page.getByText("「汎用業務 Agent のフィードバック」に評価ケースを追加しました")).toBeVisible();
  expect(mockApi.lastRequest("POST", "/api/evaluation-sets")?.body).toEqual({
    agent_id: "default",
    name: "汎用業務 Agent のフィードバック",
    description: "",
    cases: [
      {
        question: DISLIKED_QUESTION,
        expected: "15 件",
        expected_tools: ["nl2sql__nl2sql_query", "rag__rag_search"],
        source_run_id: "run-dislike-810",
      },
    ],
  });
});

test("評価ケースの下書きの読み込み中と失敗を出し、再試行できる", async ({ page, mockApi }) => {
  seedDislikedRun(mockApi);
  let failures = 1;
  await page.route("**/api/runs/run-dislike-810/evaluation-case", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 1_200));
    if (failures > 0) {
      failures -= 1;
      await route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({ data: null, error_messages: ["一時的に下書きを作れませんでした。"], warning_messages: [] }),
      });
      return;
    }
    await route.fallback();
  });
  await page.setViewportSize({ width: 375, height: 812 });
  const detail = await openDislikedDetail(page);
  await detail.getByTestId("feedback-add-case-toggle").click();
  await expect(detail.getByTestId("feedback-add-case-loading")).toContainText("評価ケースの下書きを作っています");
  await expect(detail.getByText("一時的に下書きを作れませんでした。")).toBeVisible();
  await detail.getByRole("button", { name: "再試行" }).click();
  await expect(detail.getByTestId("feedback-add-case-form").getByLabel("質問")).toHaveValue(DISLIKED_QUESTION);
  await expectNoHorizontalOverflow(page);
});

test("品質評価の権限が無い利用者と、役に立った回答には評価ケースへの追加を出さない", async ({ page, mockApi }) => {
  seedDislikedRun(mockApi);
  seedRatedRun(mockApi);
  mockApi.setCurrentUser(dbUser({ permissions: ["menu.feedback"] }));
  const detail = await openDislikedDetail(page);
  await expect(detail.getByTestId("feedback-detail-owner")).toContainText("役に立たなかった");
  await expect(detail.getByTestId("feedback-add-case-toggle")).toHaveCount(0);
});

test("役に立った回答の詳細には評価ケースへの追加を出さない", async ({ page, mockApi }) => {
  seedRatedRun(mockApi);
  await page.goto("/feedback");
  await page
    .getByRole("table", { name: "フィードバックの一覧" })
    .getByRole("button", { name: /今月の契約件数は？/ })
    .click();
  const detail = page.getByRole("dialog", { name: "フィードバックの詳細" });
  await expect(detail.getByTestId("feedback-detail-owner")).toContainText("役に立った");
  await expect(detail.getByTestId("feedback-add-case-toggle")).toHaveCount(0);
});

test("Run の詳細の管理者の評価の下から評価ケースに追加できる", async ({ page, mockApi }) => {
  seedDislikedRun(mockApi);
  seedEvaluationSets(mockApi);
  await page.goto("/runs?id=run-dislike-810");
  await page.getByTestId("run-add-case-toggle").click();
  const form = page.getByTestId("run-add-case-form");
  await expect(form.getByLabel("質問")).toHaveValue(DISLIKED_QUESTION);
  await form.getByRole("button", { name: "評価ケースに追加" }).click();
  await expect(page.getByText("「月次の評価」に評価ケースを追加しました")).toBeVisible();
  expect(mockApi.lastRequest("POST", "/api/evaluation-sets/evset-open/cases")?.body).toMatchObject({
    source_run_id: "run-dislike-810",
  });
});
