import { expect, test, type Locator, type Page, type Route } from "@playwright/test";
import { mockDatabaseGateReady } from "./_helpers/database-gate";

test.beforeEach(async ({ page }) => mockDatabaseGateReady(page));

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

const denseHistoryItem = {
  id: "history-dense-layout",
  question:
    '対象テーブル："V_EMP_DEPT" 抽出項目："V_EMP_DEPT"."EMPLOYEE_NAME" "V_EMP_DEPT"."DEPARTMENT_NAME" 抽出条件：VERY_LONG_UNBROKEN_IDENTIFIER_WITHOUT_SPACES_0123456789_ABCDEFGHIJKLMNOPQRSTUVWXYZ',
  engine: "select_ai",
  generated_sql:
    'SELECT "EMPLOYEE_NAME", "DEPARTMENT_NAME" FROM "V_EMP_DEPT" WHERE "VERY_LONG_UNBROKEN_FILTER_IDENTIFIER_0123456789" IS NOT NULL',
  executable_sql:
    'SELECT "EMPLOYEE_NAME", "DEPARTMENT_NAME" FROM "V_EMP_DEPT" WHERE "VERY_LONG_UNBROKEN_FILTER_IDENTIFIER_0123456789" IS NOT NULL',
  created_at: "2026-07-20T06:10:00.000Z",
  elapsed_ms: 6500,
  feedback_rating: null,
  profile_id: "profile-with-a-very-long-unbroken-identifier-0123456789",
  profile_name: "プロファイルビュー_WITH_A_VERY_LONG_UNBROKEN_IDENTIFIER_0123456789",
  rewritten_question:
    '対象テーブル："V_EMP_DEPT" 抽出項目："V_EMP_DEPT"."EMPLOYEE_NAME" "V_EMP_DEPT"."DEPARTMENT_NAME"',
  safety_is_safe: true,
  result_row_count: 14,
  result_columns: ["EMPLOYEE_NAME", "DEPARTMENT_NAME"],
  feedback_comment: "",
};

async function mockHistory(page: Page, items: readonly Record<string, unknown>[] = historyItems) {
  await page.route("**/api/nl2sql/history", (route) => fulfillJson(route, { items }));
}

async function hasDocumentHorizontalScroll(page: Page) {
  return page.evaluate(
    () =>
      document.documentElement.scrollWidth > document.documentElement.clientWidth + 1 ||
      document.body.scrollWidth > document.body.clientWidth + 1
  );
}

function historyRows(page: Page) {
  return page.getByTestId("history-grid").getByTestId("history-row");
}

async function expectContained(child: Locator, parent: Locator) {
  const childBox = await child.boundingBox();
  const parentBox = await parent.boundingBox();
  expect(childBox).not.toBeNull();
  expect(parentBox).not.toBeNull();
  expect(childBox!.x).toBeGreaterThanOrEqual(parentBox!.x - 1);
  expect(childBox!.y).toBeGreaterThanOrEqual(parentBox!.y - 1);
  expect(childBox!.x + childBox!.width).toBeLessThanOrEqual(parentBox!.x + parentBox!.width + 1);
  expect(childBox!.y + childBox!.height).toBeLessThanOrEqual(parentBox!.y + parentBox!.height + 1);
}

async function expectNotOverlapping(first: Locator, second: Locator) {
  const firstBox = await first.boundingBox();
  const secondBox = await second.boundingBox();
  expect(firstBox).not.toBeNull();
  expect(secondBox).not.toBeNull();
  const horizontalOverlap = Math.min(firstBox!.x + firstBox!.width, secondBox!.x + secondBox!.width) - Math.max(firstBox!.x, secondBox!.x);
  const verticalOverlap = Math.min(firstBox!.y + firstBox!.height, secondBox!.y + secondBox!.height) - Math.max(firstBox!.y, secondBox!.y);
  expect(horizontalOverlap > 0 && verticalOverlap > 0).toBe(false);
}

async function dragDividerToEdge(page: Page, pane: Locator, divider: Locator, edge: "left" | "right") {
  const paneBox = await pane.boundingBox();
  const dividerBox = await divider.boundingBox();
  expect(paneBox).not.toBeNull();
  expect(dividerBox).not.toBeNull();
  const startX = dividerBox!.x + dividerBox!.width / 2;
  const startY = dividerBox!.y + dividerBox!.height / 2;
  const targetX = edge === "left" ? paneBox!.x + 1 : paneBox!.x + paneBox!.width - 1;
  await page.mouse.move(startX, startY);
  await page.mouse.down();
  await page.mouse.move(targetX, startY, { steps: 8 });
  await page.mouse.up();
}

async function expectDenseLayoutContained(page: Page) {
  await expect.poll(() => hasDocumentHorizontalScroll(page)).toBe(false);
  const row = historyRows(page).first();
  const rowButton = row.getByRole("button");
  await expectContained(rowButton.getByTestId("history-question"), rowButton);
  const detail = page.getByTestId("history-detail");
  await expectContained(detail.getByRole("heading", { name: denseHistoryItem.question }), detail);
  await expectNotOverlapping(
    detail.getByRole("heading", { name: denseHistoryItem.question }),
    detail.getByRole("button", { name: "この質問で再実行" })
  );
}

test("実行履歴は管理一覧で検索・絞り込み・並べ替え・詳細確認できる", async ({ page }) => {
  await mockHistory(page);
  await page.goto("/history");

  await expect(historyRows(page)).toHaveCount(3);
  await expect(page.getByText("表示件数").locator("..")).toContainText("3");
  await expect(page.getByText("評価済み件数").locator("..")).toContainText("2");
  await expect(page.getByRole("button", { name: "未入金の顧客を確認 の履歴を表示" })).toHaveAttribute("aria-current", "true");
  await expect(page.getByTestId("history-detail").getByRole("heading", { name: "未入金の顧客を確認" })).toBeVisible();
  await expect(page.getByTestId("history-detail").getByText("安全", { exact: true })).toBeVisible();

  const search = page.getByRole("searchbox", { name: "履歴検索" });
  await search.fill("集計条件が違います");
  await expect(historyRows(page)).toHaveCount(1);
  await expect(page.getByRole("button", { name: "請求金額を確認 の履歴を表示" })).toHaveAttribute("aria-current", "true");

  await search.clear();
  await page.getByLabel("評価フィルタ").selectOption("unrated");
  await page.getByLabel("安全状態フィルタ").selectOption("blocked");
  await expect(historyRows(page)).toHaveCount(1);
  await expect(page.getByText("監査ログを削除", { exact: true }).first()).toBeVisible();
  await expect(page.getByTestId("history-detail").getByText("ブロック", { exact: true })).toBeVisible();

  await page.getByLabel("評価フィルタ").selectOption("all");
  await page.getByLabel("安全状態フィルタ").selectOption("all");
  await page.getByRole("button", { name: "実行情報" }).click();
  await expect(historyRows(page).first()).toContainText("監査ログを削除");

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

    await page.getByRole("button", { name: "未入金の顧客を確認 の履歴を表示" }).click();
    await expect.poll(() => page.evaluate(() => document.activeElement?.id)).toBe("history-detail-heading");
  }
  await expect(pane).toBeVisible();
  expect(await hasDocumentHorizontalScroll(page)).toBe(false);
});

test("実行履歴は長い質問を分割比率と画面幅に応じて安全に折り返す", async ({ page }) => {
  await page.setViewportSize({ width: 2048, height: 1000 });
  await page.addInitScript(() => {
    if (!window.sessionStorage.getItem("history-dense-layout-split-cleared")) {
      window.localStorage.removeItem("production-ready-nl2sql.fixedSplitPane.history-management-list");
      window.sessionStorage.setItem("history-dense-layout-split-cleared", "true");
    }
  });
  await mockHistory(page, [denseHistoryItem]);
  await page.goto("/history");

  await expect(page.getByLabel("実行履歴の状態")).toHaveAttribute("data-density", "compact");
  const selectedRow = page.getByRole("button", { name: `${denseHistoryItem.question} の履歴を表示` });
  await expect(selectedRow).toHaveAttribute("aria-current", "true");
  await expect(page.getByRole("button", { name: "実行情報: 降順" })).toHaveAttribute("aria-pressed", "true");
  await expectDenseLayoutContained(page);

  const pane = page.getByTestId("fixed-split-pane-history-management-list");
  const divider = page.getByTestId("fixed-split-pane-history-management-list-divider");
  await expect(divider).toBeVisible();

  await dragDividerToEdge(page, pane, divider, "left");
  await expect.poll(async () => Number(await pane.getAttribute("data-split-left-fraction"))).toBeLessThanOrEqual(0.251);
  await expectDenseLayoutContained(page);

  await dragDividerToEdge(page, pane, divider, "right");
  await expect.poll(async () => Number(await pane.getAttribute("data-split-left-fraction"))).toBeGreaterThanOrEqual(0.749);
  await expectDenseLayoutContained(page);

  for (const width of [1440, 1280]) {
    await page.setViewportSize({ width, height: 900 });
    await dragDividerToEdge(page, pane, divider, "left");
    await expect.poll(async () => Number(await pane.getAttribute("data-split-left-fraction"))).toBeLessThanOrEqual(0.251);
    await expectDenseLayoutContained(page);
    await dragDividerToEdge(page, pane, divider, "right");
    await expect.poll(async () => Number(await pane.getAttribute("data-split-left-fraction"))).toBeGreaterThanOrEqual(0.749);
    await expectDenseLayoutContained(page);
  }

  await page.setViewportSize({ width: 375, height: 812 });
  await expect(divider).toBeHidden();
  await expect
    .poll(async () =>
      page.getByTestId("history-filter-grid").locator("select").evaluateAll((selects) => {
        const [feedback, safety] = selects.map((select) => select.getBoundingClientRect());
        return {
          sameColumn: Math.abs(feedback.x - safety.x) < 2,
          separated: safety.y >= feedback.bottom,
        };
      })
    )
    .toEqual({ sameColumn: true, separated: true });
  await selectedRow.click();
  await expect.poll(() => page.evaluate(() => document.activeElement?.id)).toBe("history-detail-heading");
  await expectDenseLayoutContained(page);

  await page.emulateMedia({ colorScheme: "dark", reducedMotion: "reduce" });
  await page.evaluate(() => document.documentElement.classList.add("dark"));
  await expectDenseLayoutContained(page);
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
  await expect(historyRows(page)).toHaveCount(3);
});

test("実行履歴の評価フィルタに要確認は表示しない", async ({ page }) => {
  await mockHistory(page);
  await page.goto("/history");

  const options = page.getByLabel("評価フィルタ").locator("option");
  await expect(options).toHaveText(["すべて", "未評価", "良い", "違う"]);
  await expect(options.filter({ hasText: "要確認" })).toHaveCount(0);
});

test("実行履歴一覧は 10 件ごとにページ送りする", async ({ page }) => {
  const manyItems = Array.from({ length: 12 }, (_, index) => ({
    id: `history-${index}`,
    question: `質問 ${String(index).padStart(2, "0")}`,
    engine: "select_ai",
    generated_sql: "SELECT 1 FROM DUAL",
    executable_sql: "SELECT 1 FROM DUAL",
    created_at: `2026-06-${String(28 - index).padStart(2, "0")}T10:00:00.000Z`,
    elapsed_ms: 100,
    feedback_rating: null,
    profile_id: "default",
    profile_name: "既定プロファイル",
    rewritten_question: "",
    safety_is_safe: true,
    result_row_count: 1,
    result_columns: ["N"],
    feedback_comment: "",
  }));
  await mockHistory(page, manyItems);
  await page.goto("/history");

  const rows = historyRows(page);
  await expect(rows).toHaveCount(10);
  await expect(page.getByTestId("history-pagination")).toBeVisible();

  await page.getByTestId("history-pagination").getByRole("button", { name: "次へ" }).click();
  await expect(rows).toHaveCount(2);
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
  await expect(historyRows(page)).toHaveCount(3);
});
