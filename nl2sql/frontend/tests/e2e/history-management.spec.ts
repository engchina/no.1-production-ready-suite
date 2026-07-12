import { expect, test, type Page, type Route } from "@playwright/test";

async function fulfillJson(route: Route, data: unknown) {
  await route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ data }),
  });
}

const historyItems = [
  {
    id: "history-new",
    question: "未入金の顧客を確認",
    engine: "select_ai_agent",
    generated_sql: "SELECT CUSTOMER_NAME FROM INVOICES WHERE PAID_AT IS NULL",
    executable_sql: "SELECT CUSTOMER_NAME FROM INVOICES WHERE PAID_AT IS NULL",
    created_at: "2026-06-22T10:00:00.000Z",
    elapsed_ms: 250,
    feedback_rating: "good",
    profile_id: "finance",
    profile_name: "経理プロファイル",
    rewritten_question: "未入金顧客",
    safety_is_safe: true,
    result_row_count: 2,
    result_columns: ["CUSTOMER_NAME"],
    feedback_comment: "期待通りです",
  },
  {
    id: "history-middle",
    question: "請求金額を確認",
    engine: "select_ai",
    generated_sql: "SELECT TOTAL_AMOUNT FROM INVOICES",
    executable_sql: "SELECT TOTAL_AMOUNT FROM INVOICES",
    created_at: "2026-06-21T10:00:00.000Z",
    elapsed_ms: 120,
    feedback_rating: "bad",
    profile_id: "default",
    profile_name: "既定プロファイル",
    rewritten_question: "請求金額一覧",
    safety_is_safe: true,
    result_row_count: 4,
    result_columns: ["TOTAL_AMOUNT"],
    feedback_comment: "集計条件が違います",
  },
  {
    id: "history-old",
    question: "監査ログを削除",
    engine: "select_ai",
    generated_sql: "DELETE FROM AUDIT_LOG",
    executable_sql: "",
    created_at: "2026-06-20T10:00:00.000Z",
    elapsed_ms: 30,
    feedback_rating: null,
    profile_id: "audit",
    profile_name: "監査プロファイル",
    rewritten_question: "",
    safety_is_safe: false,
    result_row_count: 0,
    result_columns: [],
    feedback_comment: "",
  },
];

async function mockHistory(page: Page, items = historyItems) {
  await page.route("**/api/nl2sql/history", (route) => fulfillJson(route, { items }));
}

async function hasDocumentHorizontalScroll(page: Page) {
  return page.evaluate(
    () =>
      document.documentElement.scrollWidth > document.documentElement.clientWidth + 1 ||
      document.body.scrollWidth > document.body.clientWidth + 1
  );
}

test("実行履歴は管理テーブルで検索・絞り込み・並べ替え・詳細確認できる", async ({ page }) => {
  await mockHistory(page);
  await page.goto("/history");

  await expect(page.getByTestId("history-grid").locator("tbody tr")).toHaveCount(3);
  await expect(page.getByText("表示件数").locator("..")).toContainText("3");
  await expect(page.getByText("評価済み件数").locator("..")).toContainText("2");
  await expect(page.getByRole("button", { name: "未入金の顧客を確認 の履歴を表示" })).toHaveAttribute("aria-current", "true");
  await expect(page.getByTestId("history-detail").getByRole("heading", { name: "未入金の顧客を確認" })).toBeVisible();
  await expect(page.getByTestId("history-detail").getByText("安全", { exact: true })).toBeVisible();

  const search = page.getByRole("searchbox", { name: "履歴検索" });
  await search.fill("集計条件が違います");
  await expect(page.getByTestId("history-grid").locator("tbody tr")).toHaveCount(1);
  await expect(page.getByRole("button", { name: "請求金額を確認 の履歴を表示" })).toHaveAttribute("aria-current", "true");

  await search.clear();
  await page.getByLabel("評価フィルタ").selectOption("unrated");
  await page.getByLabel("安全状態フィルタ").selectOption("blocked");
  await expect(page.getByTestId("history-grid").locator("tbody tr")).toHaveCount(1);
  await expect(page.getByText("監査ログを削除", { exact: true }).first()).toBeVisible();
  await expect(page.getByTestId("history-detail").getByText("ブロック", { exact: true })).toBeVisible();

  await page.getByLabel("評価フィルタ").selectOption("all");
  await page.getByLabel("安全状態フィルタ").selectOption("all");
  await page.getByRole("button", { name: "実行情報" }).click();
  await expect(page.getByTestId("history-grid").locator("tbody tr").first()).toContainText("監査ログを削除");

  await page.getByRole("button", { name: "請求金額を確認 の履歴を表示" }).click();
  const overviewTab = page.getByRole("tab", { name: "概要" });
  await overviewTab.focus();
  await overviewTab.press("ArrowRight");
  const sqlTab = page.getByRole("tab", { name: "SQL" });
  await expect(sqlTab).toHaveAttribute("aria-selected", "true");
  await expect(page.getByTestId("history-detail").locator("pre")).toContainText("SELECT TOTAL_AMOUNT FROM INVOICES");

  const pane = page.getByTestId("fixed-split-pane-history-management-list");
  const divider = page.getByTestId("fixed-split-pane-history-management-list-divider");
  const viewport = page.viewportSize();
  if ((viewport?.width ?? 0) >= 1280) {
    await expect(divider).toBeVisible();
    await expect(divider).toHaveAttribute("role", "separator");
  } else {
    await expect(divider).toBeHidden();
    const left = await page.getByTestId("fixed-split-pane-history-management-list-left").boundingBox();
    const right = await page.getByTestId("fixed-split-pane-history-management-list-right").boundingBox();
    expect(left).not.toBeNull();
    expect(right).not.toBeNull();
    expect(right!.y).toBeGreaterThan(left!.y);

    await page.getByRole("button", { name: "詳細" }).first().click();
    await expect.poll(() => page.evaluate(() => document.activeElement?.id)).toBe("history-detail-heading");
  }
  await expect(pane).toBeVisible();
  expect(await hasDocumentHorizontalScroll(page)).toBe(false);
});

test("実行履歴は初期読込と空状態を明示する", async ({ page }) => {
  await page.route("**/api/nl2sql/history", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 300));
    await fulfillJson(route, { items: [] });
  });

  await page.goto("/history");
  await expect(page.getByTestId("history-list-skeleton")).toBeVisible();
  await expect(page.getByTestId("history-detail-skeleton")).toBeVisible();
  await expect(page.getByText("履歴はまだありません")).toBeVisible();
  expect(await hasDocumentHorizontalScroll(page)).toBe(false);
});

test("実行履歴は検索結果なしと条件クリアを案内する", async ({ page }) => {
  await mockHistory(page);
  await page.goto("/history");

  await page.getByRole("searchbox", { name: "履歴検索" }).fill("一致しない検索語");
  await expect(page.getByText("条件に一致する履歴がありません")).toBeVisible();
  await page.getByRole("button", { name: "条件をクリア" }).click();
  await expect(page.getByTestId("history-grid").locator("tbody tr")).toHaveCount(3);
});

test("実行履歴は読込失敗を既存データなしでも再試行できる", async ({ page }) => {
  let requestCount = 0;
  await page.route("**/api/nl2sql/history", async (route) => {
    requestCount += 1;
    if (requestCount === 1) {
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ detail: "履歴サービスを利用できません。" }),
      });
      return;
    }
    await fulfillJson(route, { items: historyItems });
  });

  await page.goto("/history");
  const alert = page.getByRole("alert");
  await expect(alert).toContainText("履歴サービスを利用できません。");
  await expect(alert).toContainText("通信状態を確認して再試行してください。");
  await alert.getByRole("button", { name: "履歴更新" }).click();
  await expect(page.getByTestId("history-grid").locator("tbody tr")).toHaveCount(3);
});
