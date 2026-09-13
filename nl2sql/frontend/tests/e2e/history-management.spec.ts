import { expectLocalUiFonts } from "./_helpers/local-fonts";
import { expect, test, type Locator, type Page, type Route } from "@playwright/test";
import { mockDatabaseGateReady, systemAdminMe } from "./_helpers/database-gate";
import { expectCompactSortHeaders, expectPlainSortHeader } from "./_helpers/sort-header";

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
    generation_elapsed_ms: 180,
    engine_timings: [
      {
        engine: "select_ai_agent",
        elapsed_ms: 80,
        status: "failed",
        error: "team execution failed",
      },
      {
        engine: "enterprise_ai_direct",
        elapsed_ms: 180,
        status: "success",
        error: "",
      },
    ],
    stage_timings: [
      { stage: "prepare_context", elapsed_ms: 20 },
      { stage: "generate_sql", elapsed_ms: 190 },
      { stage: "safety_check", elapsed_ms: 15 },
      { stage: "execute_sql", elapsed_ms: 25 },
      { stage: "format_results", elapsed_ms: 5 },
    ],
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
    '対象テーブル："部署情報を管理するテーブル"\n抽出項目：\n抽出条件：VERY_LONG_UNBROKEN_IDENTIFIER_WITHOUT_SPACES_0123456789_ABCDEFGHIJKLMNOPQRSTUVWXYZ',
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

function historySearchText(item: Record<string, unknown>) {
  return [
    item.question,
    item.generated_sql,
    item.feedback_comment,
  ]
    .join("\n")
    .toLocaleLowerCase("ja-JP");
}

function historyItemsForRequest(url: URL, items: readonly Record<string, unknown>[]) {
  const q = (url.searchParams.get("q") ?? "").trim().toLocaleLowerCase("ja-JP");
  const rating = url.searchParams.get("rating") ?? "all";
  const safety = url.searchParams.get("safety") ?? "all";
  return items.filter((item) => {
    if (rating === "unrated" && item.feedback_rating) return false;
    if (rating !== "all" && rating !== "unrated" && item.feedback_rating !== rating) return false;
    if (safety === "safe" && item.safety_is_safe !== true) return false;
    if (safety === "blocked" && item.safety_is_safe !== false) return false;
    return !q || historySearchText(item).includes(q);
  });
}

async function mockHistory(
  page: Page,
  items: readonly Record<string, unknown>[] = historyItems,
  requests: URL[] = []
) {
  await page.route("**/api/nl2sql/history**", (route) => {
    const url = new URL(route.request().url());
    requests.push(url);
    const filtered = historyItemsForRequest(url, items);
    return fulfillJson(route, { items: filtered, next_cursor: "", total: filtered.length });
  });
}

function createRequestGate() {
  let release!: () => void;
  const promise = new Promise<void>((resolve) => {
    release = resolve;
  });
  return { promise, release };
}

async function hasDocumentHorizontalScroll(page: Page) {
  return page.evaluate(
    () =>
      document.documentElement.scrollWidth > document.documentElement.clientWidth + 1 ||
      document.body.scrollWidth > document.body.clientWidth + 1
  );
}

async function waitForAnimationFrames(page: Page) {
  await page.evaluate(
    () =>
      new Promise<void>((resolve) => {
        window.requestAnimationFrame(() => window.requestAnimationFrame(() => resolve()));
      })
  );
}

async function expectMainScrollPreserved(page: Page, action: () => Promise<void>) {
  const main = page.getByRole("main");
  const before = await main.evaluate((node) => node.scrollTop);
  await action();
  await waitForAnimationFrames(page);
  await expect
    .poll(async () => {
      const after = await main.evaluate((node) => node.scrollTop);
      return Math.abs(after - before);
    })
    .toBeLessThanOrEqual(2);
}

async function expectMainScrollPreservedAfterClick(page: Page, locator: Locator) {
  await locator.scrollIntoViewIfNeeded();
  await waitForAnimationFrames(page);
  await expectMainScrollPreserved(page, async () => {
    await locator.click();
  });
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

async function expectQuestionLineClamp(question: Locator, lines: number) {
  const metrics = await question.evaluate((element, expectedLines) => {
    const style = window.getComputedStyle(element);
    const lineHeight = Number.parseFloat(style.lineHeight);
    const height = element.getBoundingClientRect().height;
    return {
      clamp: style.getPropertyValue("-webkit-line-clamp"),
      height,
      lineHeight,
      maxHeight: Number.isFinite(lineHeight) ? lineHeight * expectedLines + 8 : null,
      overflow: style.overflow,
    };
  }, lines);
  expect(metrics.clamp).toBe(String(lines));
  expect(metrics.overflow).toBe("hidden");
  if (metrics.maxHeight !== null) {
    expect(metrics.height).toBeLessThanOrEqual(metrics.maxHeight);
  }
}

async function expectQuestionWeightBelow(question: Locator, maxWeight: number) {
  const fontWeight = await question.evaluate((element) =>
    Number.parseInt(window.getComputedStyle(element).fontWeight, 10)
  );
  expect(fontWeight).toBeLessThan(maxWeight);
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

async function expectSplitPaneReservedTrack(page: Page, edge: "left" | "right") {
  const pane = page.getByTestId("fixed-split-pane-history-management-list");
  const left = await page.getByTestId("fixed-split-pane-history-management-list-left").boundingBox();
  const divider = await page.getByTestId("fixed-split-pane-history-management-list-divider").boundingBox();
  const right = await page.getByTestId("fixed-split-pane-history-management-list-right").boundingBox();
  expect(left).not.toBeNull();
  expect(divider).not.toBeNull();
  expect(right).not.toBeNull();
  expect(left!.width).toBeGreaterThanOrEqual(319);
  expect(right!.width).toBeGreaterThanOrEqual(319);
  expect(left!.x + left!.width).toBeLessThanOrEqual(divider!.x + 1);
  expect(divider!.x + divider!.width).toBeLessThanOrEqual(right!.x + 1);
  const fraction = Number(await pane.getAttribute("data-split-left-fraction"));
  if (edge === "left") expect(fraction).toBeLessThanOrEqual(0.5);
  else expect(fraction).toBeGreaterThanOrEqual(0.5);
}

async function expectDenseLayoutContained(page: Page) {
  await expect.poll(() => hasDocumentHorizontalScroll(page)).toBe(false);
  const row = historyRows(page).first();
  const rowButton = row.getByRole("button");
  const rowQuestion = rowButton.getByTestId("history-question");
  await expectContained(rowQuestion, rowButton);
  await expectQuestionLineClamp(rowQuestion, 1);
  await expectQuestionWeightBelow(rowQuestion, 600);
  const detail = page.getByTestId("history-detail");
  await expectContained(detail.getByTestId("history-detail-question"), detail);
  await expectNotOverlapping(
    detail.getByTestId("history-detail-question"),
    detail.getByRole("button", { name: "この質問で再実行" })
  );
  await expectSameWidth(
    detail.getByTestId("history-detail-question-block"),
    detail.getByTestId("history-detail-rewritten-block")
  );
}

async function expectSameWidth(a: Locator, b: Locator) {
  const boxA = await a.boundingBox();
  const boxB = await b.boundingBox();
  expect(boxA).not.toBeNull();
  expect(boxB).not.toBeNull();
  expect(Math.abs(boxA!.width - boxB!.width)).toBeLessThanOrEqual(1);
  expect(Math.abs(boxA!.x - boxB!.x)).toBeLessThanOrEqual(1);
}

test("管理者は実行ユーザーを一覧と詳細で確認し同名・削除済み・未記録を区別できる", async ({ page }, testInfo) => {
  const items = historyItems.map((item, index) => ({
    ...item,
    actor_user_uuid: `actor-${index}`,
    actor_login_user_id: `analyst-${index}`,
    actor_display_name: "同じ表示名",
  }));
  items.push({ ...items[0], id: "deleted", question: "削除済みユーザーの履歴", actor_user_uuid: "deleted-user", actor_login_user_id: "", actor_display_name: "" });
  items.push({ ...items[0], id: "legacy", question: "古い履歴", actor_user_uuid: "", actor_login_user_id: "", actor_display_name: "" });
  items[2].actor_display_name = "長い表示名".repeat(12);
  items[2].actor_login_user_id = "long_login_id_".repeat(10);
  await mockHistory(page, items);
  await page.goto("/history");
  const first = page.getByRole("button", { name: "未入金の顧客を確認 の履歴を表示" });
  await expect(first.getByTestId("history-executor")).toHaveText("実行ユーザー: 同じ表示名（analyst-0）");
  const second = page.getByRole("button", { name: "請求金額を確認 の履歴を表示" });
  await second.focus();
  await page.keyboard.press("Enter");
  const detail = page.getByTestId("history-detail").getByTestId("history-executor");
  await expect(detail).toContainText("同じ表示名（analyst-1）");
  await expect(detail).toContainText("ユーザー UUID: actor-1");
  await expect(detail).toContainText("表示名とログイン ID は現在のユーザー情報です。");
  await page.getByRole("button", { name: "削除済みユーザーの履歴 の履歴を表示" }).click();
  await expect(detail).toContainText("ユーザー情報なし（deleted-user）");
  await page.getByRole("button", { name: "古い履歴 の履歴を表示" }).click();
  await expect(detail).toHaveText("実行ユーザー: 記録なし");
  const longRow = page.getByRole("button", { name: "監査ログを削除 の履歴を表示" });
  await longRow.click();
  await expectContained(longRow.getByTestId("history-executor"), longRow);
  await expectContained(detail, page.getByTestId("history-detail"));
  expect(await hasDocumentHorizontalScroll(page)).toBe(false);
  await page.getByTestId("history-detail-header").screenshot({ path: testInfo.outputPath("history-executor-detail.png") });
  await longRow.screenshot({ path: testInfo.outputPath("history-executor-row.png") });
});

test("一般ユーザーには管理者向けの実行ユーザー情報を表示しない", async ({ page }) => {
  await page.route("**/api/auth/me", (route) => fulfillJson(route, {
    ...systemAdminMe, is_system_admin: false, role_codes: ["ANALYST"], permissions: ["menu.history"],
  }));
  await mockHistory(page, historyItems.map((item) => ({
    ...item, actor_user_uuid: "other-user", actor_login_user_id: "other-login", actor_display_name: "他ユーザー",
  })));
  await page.goto("/history");
  await expect(historyRows(page)).toHaveCount(3);
  await expect(page.getByTestId("history-executor")).toHaveCount(0);
  await expect(page.getByText(/他ユーザー|other-login/)).toHaveCount(0);
});

test("実行履歴の安全とブロックの定義は操作なしで読めて履歴選択でも参照できる", async ({ page }, testInfo) => {
  await mockHistory(page);
  await page.goto("/history");
  const help = page.getByRole("region", { name: "安全状態の見方" });
  const safeDefinition = "参照用の SQL（SELECT / WITH）で、許可された表・列や危険な処理の有無などの検査を通過しています。";
  const blockedDefinition = "更新・削除などの処理、許可外の表・列の参照、SQL を解析できない場合など、検査を通過せず実行を止めた状態です。";
  for (const definition of [safeDefinition, blockedDefinition]) {
    const text = help.getByText(definition, { exact: true });
    await expect(text).toBeVisible();
    await expect(text).toHaveCSS("font-size", "14px");
    await expectContained(text, help);
  }
  await expect(help.getByText("安全", { exact: true })).toBeVisible();
  await expect(help.getByText("ブロック", { exact: true })).toBeVisible();
  await expect(help).toContainText("「安全」は結果の正しさや再実行の成功を保証するものではなく、再実行時には改めて検査します。");
  await help.screenshot({ path: testInfo.outputPath("history-safety-help.png") });
  await page.locator("html").evaluate((element) => element.classList.add("dark"));
  await help.screenshot({ path: testInfo.outputPath("history-safety-help-dark.png") });
  await page.locator("html").evaluate((element) => element.classList.remove("dark"));
  expect(await hasDocumentHorizontalScroll(page)).toBe(false);

  const safeRow = page.getByRole("button", { name: "未入金の顧客を確認 の履歴を表示" });
  await expect(safeRow).toHaveAccessibleDescription(safeDefinition);
  await safeRow.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("history-detail").getByText("安全", { exact: true })).toBeVisible();
  const blockedRow = page.getByRole("button", { name: "監査ログを削除 の履歴を表示" });
  await expect(blockedRow).toHaveAccessibleDescription(blockedDefinition);
  await blockedRow.focus();
  await page.keyboard.press("Space");
  await expect(page.getByTestId("history-detail").getByText("ブロック", { exact: true })).toBeVisible();
  await expect(blockedRow).toHaveAttribute("aria-current", "true");
});

test("実行履歴は管理一覧で検索・絞り込み・並べ替え・詳細確認できる", async ({ page }) => {
  const historyRequests: URL[] = [];
  await mockHistory(page, historyItems, historyRequests);
  await page.goto("/history");

  await expect(historyRows(page)).toHaveCount(3);
  await expect(page.getByLabel("実行履歴の状態")).toHaveCount(0);
  await expect(page.getByText("3 件", { exact: true })).toBeVisible();
  await expect(page.getByText("評価済み件数", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "表示を更新", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "未入金の顧客を確認 の履歴を表示" })).toHaveAttribute("aria-current", "true");
  await expect(page.getByTestId("history-detail").getByRole("heading", { name: "履歴詳細" })).toBeVisible();
  await expect(page.getByTestId("history-detail-question")).toContainText("未入金の顧客を確認");
  await expect(page.getByTestId("history-detail").getByText("安全", { exact: true })).toBeVisible();
  await expect(historyRows(page).first().getByText("利用者評価: 良い", { exact: true })).toBeVisible();
  await expect(historyRows(page).first().getByText("生成 180ms", { exact: true })).toBeVisible();
  await expect(page.getByTestId("history-detail").getByText("利用者評価: 良い", { exact: true })).toBeVisible();
  const timingBreakdown = page.getByTestId("history-timing-breakdown");
  await expect(timingBreakdown).toContainText("処理時間");
  await expect(timingBreakdown).toContainText("合計");
  await expect(timingBreakdown).toContainText("250ms");
  await expect(timingBreakdown).toContainText("生成");
  await expect(timingBreakdown).toContainText("安全確認");
  await expect(timingBreakdown).toContainText("実行");
  await expect(timingBreakdown).toContainText("Select AI Agent");
  await expect(timingBreakdown).toContainText("失敗");
  await expect(timingBreakdown).toContainText("Enterprise AI Direct");
  await expect(timingBreakdown).toContainText("成功");

  const search = page.getByRole("searchbox", { name: "履歴検索" });
  await search.fill("集計条件が違います");
  await expect(historyRows(page)).toHaveCount(1);
  await expect
    .poll(() => historyRequests.some((url) => url.searchParams.get("q") === "集計条件が違います"))
    .toBe(true);
  await expect(page.getByRole("button", { name: "請求金額を確認 の履歴を表示" })).toHaveAttribute("aria-current", "true");

  await search.clear();
  await page.getByLabel("利用者評価フィルター").selectOption("unrated");
  await page.getByLabel("安全状態フィルタ").selectOption("blocked");
  await expect(historyRows(page)).toHaveCount(1);
  await expect.poll(() => {
    const last = historyRequests.at(-1);
    return {
      rating: last?.searchParams.get("rating") ?? "all",
      safety: last?.searchParams.get("safety") ?? "all",
    };
  }).toEqual({ rating: "unrated", safety: "blocked" });
  await expect(page.getByText("監査ログを削除", { exact: true }).first()).toBeVisible();
  await expect(page.getByTestId("history-detail").getByText("ブロック", { exact: true })).toBeVisible();

  await page.getByLabel("利用者評価フィルター").selectOption("all");
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

    const firstHistoryButton = page.getByRole("button", { name: "未入金の顧客を確認 の履歴を表示" });
    await expectMainScrollPreservedAfterClick(page, firstHistoryButton);
    await expect(firstHistoryButton).toHaveAttribute("aria-current", "true");
    await expect(page.getByTestId("history-detail-question")).toContainText("未入金の顧客を確認");
  }
  await expect(pane).toBeVisible();
  expect(await hasDocumentHorizontalScroll(page)).toBe(false);
});

test("履歴の並べ替え見出しは従来の小さい文字とキーボード操作を維持する", async ({ page }, testInfo) => {
  await mockHistory(page);
  await page.goto("/history");
  await expect(historyRows(page)).toHaveCount(3);

  const sortGroup = page.getByRole("group", { name: "履歴一覧の並べ替え" });
  await expectCompactSortHeaders(sortGroup);
  const questionSort = sortGroup.getByRole("button", { name: "質問" });
  const executionSort = sortGroup.getByRole("button", { name: "実行情報" });
  const rootFontSize = await page.evaluate(() =>
    Number.parseFloat(getComputedStyle(document.documentElement).fontSize)
  );
  for (const button of [questionSort, executionSort]) {
    await expect(button).toHaveCSS("font-size", `${rootFontSize * 0.75}px`);
    await expect(button).toHaveCSS("font-weight", "600");
    await expect(button).toHaveCSS("height", testInfo.project.name === "mobile-375" ? "44px" : "32px");
  }

  await questionSort.focus();
  await expect(questionSort).toBeFocused();
  await questionSort.press("Enter");
  await expect(questionSort).toHaveAttribute("aria-pressed", "true");
  await page.keyboard.press("Tab");
  await expect(executionSort).toBeFocused();
  await expect(executionSort.locator("span")).toHaveCSS("text-decoration-line", "underline");
  await executionSort.press("Space");
  await expect(executionSort).toHaveAttribute("aria-pressed", "true");
  await expect(questionSort).toHaveAttribute("aria-pressed", "false");
  await expectPlainSortHeader(executionSort);
  await expect(historyRows(page).first()).toContainText("監査ログを削除");
  await expectContained(questionSort, sortGroup);
  await expectContained(executionSort, sortGroup);
  expect(await hasDocumentHorizontalScroll(page)).toBe(false);
  await questionSort.click();
  await expect(questionSort).toHaveAttribute("aria-pressed", "true");
  await expectPlainSortHeader(questionSort);
  await sortGroup.screenshot({ path: testInfo.outputPath("history-sort-font.png") });
});

test("実行履歴行クリックは主スクロールを保持して詳細だけ切り替える", async ({ page }) => {
  await mockHistory(page);
  await page.goto("/history");

  const firstHistoryButton = page.getByRole("button", { name: "未入金の顧客を確認 の履歴を表示" });
  const secondHistoryButton = page.getByRole("button", { name: "請求金額を確認 の履歴を表示" });
  const thirdHistoryButton = page.getByRole("button", { name: "監査ログを削除 の履歴を表示" });

  await expect(firstHistoryButton).toHaveAttribute("aria-current", "true");
  await expect(page.getByTestId("history-detail-question")).toContainText("未入金の顧客を確認");

  await expectMainScrollPreservedAfterClick(page, secondHistoryButton);
  await expect(secondHistoryButton).toHaveAttribute("aria-current", "true");
  await expect(firstHistoryButton).not.toHaveAttribute("aria-current", "true");
  await expect(page.getByTestId("history-detail-question")).toContainText("請求金額を確認");
  expect(await page.evaluate(() => document.activeElement?.id)).not.toBe("history-detail-heading");

  await expectMainScrollPreservedAfterClick(page, thirdHistoryButton);
  await expect(thirdHistoryButton).toHaveAttribute("aria-current", "true");
  await expect(secondHistoryButton).not.toHaveAttribute("aria-current", "true");
  await expect(page.getByTestId("history-detail-question")).toContainText("監査ログを削除");
  expect(await page.evaluate(() => document.activeElement?.id)).not.toBe("history-detail-heading");
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

  await expect(page.getByLabel("実行履歴の状態")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "表示を更新", exact: true })).toBeVisible();
  const selectedRow = historyRows(page).first().getByRole("button");
  const selectedRowQuestion = selectedRow.getByTestId("history-question");
  await expect(selectedRow).toHaveAttribute("aria-current", "true");
  await expect(selectedRowQuestion).toHaveAttribute("title", denseHistoryItem.question);
  await expect(selectedRowQuestion).toContainText('対象テーブル："部署情報を管理するテーブル" 抽出項目： 抽出条件：');
  await expect(page.getByRole("button", { name: "実行情報: 降順" })).toHaveAttribute("aria-pressed", "true");
  await expectDenseLayoutContained(page);
  await expectQuestionLineClamp(page.getByTestId("history-detail-question"), 3);
  await expectQuestionWeightBelow(page.getByTestId("history-detail-question"), 600);
  const expandQuestionButton = page.getByRole("button", { name: "全文表示" });
  await expect(expandQuestionButton).toBeVisible();
  await expect(expandQuestionButton.locator('svg[data-state]')).toHaveAttribute("data-state", "collapsed");
  await expandQuestionButton.click();
  const collapseQuestionButton = page.getByRole("button", { name: "閉じる" });
  await expect(collapseQuestionButton).toBeVisible();
  await expect(collapseQuestionButton.locator('svg[data-state]')).toHaveAttribute("data-state", "expanded");
  await expect(page.getByTestId("history-detail-question")).toContainText("VERY_LONG_UNBROKEN_IDENTIFIER");
  await expectDenseLayoutContained(page);
  await collapseQuestionButton.click();
  await expectQuestionLineClamp(page.getByTestId("history-detail-question"), 3);

  const pane = page.getByTestId("fixed-split-pane-history-management-list");
  const divider = page.getByTestId("fixed-split-pane-history-management-list-divider");
  await expect(divider).toBeVisible();

  await dragDividerToEdge(page, pane, divider, "left");
  await expectSplitPaneReservedTrack(page, "left");
  await expectDenseLayoutContained(page);

  await dragDividerToEdge(page, pane, divider, "right");
  await expectSplitPaneReservedTrack(page, "right");
  await expectDenseLayoutContained(page);

  for (const width of [1440, 1280]) {
    await page.setViewportSize({ width, height: 900 });
    await dragDividerToEdge(page, pane, divider, "left");
    await expectSplitPaneReservedTrack(page, "left");
    await expectDenseLayoutContained(page);
    await dragDividerToEdge(page, pane, divider, "right");
    await expectSplitPaneReservedTrack(page, "right");
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
  await expectMainScrollPreservedAfterClick(page, selectedRow);
  await expect(selectedRow).toHaveAttribute("aria-current", "true");
  await expect(page.getByTestId("history-detail-question")).toContainText("VERY_LONG_UNBROKEN_IDENTIFIER");
  expect(await page.evaluate(() => document.activeElement?.id)).not.toBe("history-detail-heading");
  await expectDenseLayoutContained(page);

  await page.emulateMedia({ colorScheme: "dark", reducedMotion: "reduce" });
  await page.evaluate(() => document.documentElement.classList.add("dark"));
  const darkModeChevron = page
    .getByRole("button", { name: "全文表示" })
    .locator('svg[data-state="collapsed"]');
  await expect(darkModeChevron).toBeVisible();
  await expect.poll(() => darkModeChevron.evaluate((icon) => getComputedStyle(icon).rotate)).toBe("90deg");
  await expectDenseLayoutContained(page);
});

test("実行履歴は初期読込と空状態を明示する", async ({ page }) => {
  const historyGate = createRequestGate();
  await page.route("**/api/nl2sql/history", async (route) => {
    await historyGate.promise;
    await fulfillJson(route, { items: [] });
  });

  await page.goto("/history");
  await expect(page.getByTestId("history-list-skeleton")).toBeVisible();
  await expect(page.getByTestId("history-detail-skeleton")).toBeVisible();
  await expect(page.getByRole("region", { name: "安全状態の見方" })).toBeVisible();
  await expect(
    page.getByTestId("history-list-loading").getByRole("timer")
  ).toHaveAccessibleName(/経過時間 00:0\d/);
  await expect(page.getByTestId("history-detail-skeleton")).toContainText(
    "履歴詳細を読み込んでいます",
  );
  historyGate.release();
  await expect(page.getByText("履歴はまだありません")).toBeVisible();
  await expect(page.getByRole("region", { name: "安全状態の見方" })).toBeVisible();
  expect(await hasDocumentHorizontalScroll(page)).toBe(false);
});

test("実行履歴は検索結果なしと条件クリアを案内する", async ({ page }) => {
  await mockHistory(page);
  await page.goto("/history");

  await page.getByRole("searchbox", { name: "履歴検索" }).fill("一致しない検索語");
  await expect(page.getByText("条件に一致する履歴がありません")).toBeVisible();
  await page.getByRole("button", { name: "絞り込みを解除" }).click();
  await expect(historyRows(page)).toHaveCount(3);
});

test("実行履歴の利用者評価フィルターに要確認は表示しない", async ({ page }) => {
  await mockHistory(page);
  await page.goto("/history");

  const options = page.getByLabel("利用者評価フィルター").locator("option");
  await expect(options).toHaveText(["すべて", "未評価", "良い", "違う"]);
  await expect(options.filter({ hasText: "要確認" })).toHaveCount(0);
});

test("実行履歴一覧はサーバページをローカルで二重ページングしない", async ({ page }) => {
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
  const listSurface = page.getByTestId("history-list-surface");
  const loadMore = page.getByTestId("history-load-more");
  await expect(rows).toHaveCount(12);
  await expect(page.getByTestId("history-pagination")).toHaveCount(0);
  await expect(loadMore).toContainText("12 / 12 件を読込済み");
  await expect(listSurface.getByTestId("history-pagination")).toHaveCount(0);
  await expectContained(loadMore, page.getByTestId("fixed-split-pane-history-management-list-left"));
  await expect.poll(() => hasDocumentHorizontalScroll(page)).toBe(false);

  const listSurfaceBox = await listSurface.boundingBox();
  const loadMoreBox = await loadMore.boundingBox();
  expect(listSurfaceBox).not.toBeNull();
  expect(loadMoreBox).not.toBeNull();
  expect(loadMoreBox!.y).toBeGreaterThanOrEqual(listSurfaceBox!.y + listSurfaceBox!.height + 7);
});

test("実行履歴は読込失敗を既存データなしでも再試行できる", async ({ page }) => {
  let shouldFail = true;
  await page.route("**/api/nl2sql/history", async (route) => {
    if (shouldFail) {
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
  await expect(page.getByRole("region", { name: "安全状態の見方" })).toBeVisible();
  shouldFail = false;
  await alert.getByRole("button", { name: "履歴更新" }).click();
  await expect(historyRows(page)).toHaveCount(3);
});

test("実行履歴は続きがあるとき「さらに読み込む」で追加取得する", async ({ page }) => {
  const extra = { ...historyItems[0], id: "hist-extra", question: "追加で読み込んだ履歴" };
  await page.route("**/api/nl2sql/history**", (route) => {
    const url = new URL(route.request().url());
    if (url.searchParams.get("cursor") === "next-1") {
      return fulfillJson(route, { items: [extra], next_cursor: "", total: 4 });
    }
    return fulfillJson(route, { items: historyItems, next_cursor: "next-1", total: 4 });
  });
  await page.goto("/history");

  await expect(historyRows(page)).toHaveCount(3);
  await expect(page.getByTestId("history-load-more")).toContainText("3 / 4 件を読込済み");
  await page.getByRole("button", { name: "さらに読み込む" }).click();
  await expect(historyRows(page)).toHaveCount(4);
  await expect(page.getByTestId("history-load-more")).toContainText("4 / 4 件を読込済み");
  await expect(page.getByRole("button", { name: "さらに読み込む" })).toHaveCount(0);
});

test("履歴の選択とSQLタブを往復・再読込で復元し失効時は別履歴へ切り替えない", async ({ page }, testInfo) => {
  let items = [...historyItems];
  await page.route("**/api/nl2sql/history**", (route) => fulfillJson(route, { items, next_cursor: "", total: items.length }));
  await page.goto("/history");
  await page.getByRole("button", { name: "請求金額を確認 の履歴を表示", exact: true }).click();
  const sqlTab = page.getByRole("tab", { name: "SQL", exact: true });
  await sqlTab.click();
  await page.getByRole("link", { name: "SELECT SQL を実行", exact: true }).click();
  await expect(page).toHaveURL(/\/direct-sql$/);
  await page.goBack();
  await expect(page.getByTestId("history-detail-question")).toContainText("請求金額を確認");
  await expect(sqlTab).toHaveAttribute("aria-selected", "true");
  await page.reload();
  await expect(page.getByTestId("history-detail-question")).toContainText("請求金額を確認");
  await expect(sqlTab).toHaveAttribute("aria-selected", "true");
  await sqlTab.scrollIntoViewIfNeeded();
  await page.screenshot({ path: testInfo.outputPath("history-restored-sql-tab.png") });
  items = items.filter((item) => item.question !== "請求金額を確認");
  await page.reload();
  await expect(page.getByText("選択した履歴は現在の一覧にありません。追加読込するか、一覧から履歴を選択してください。")).toBeVisible();
  await expect(page.getByTestId("history-detail")).toHaveCount(0);
  await page.getByRole("button", { name: "未入金の顧客を確認 の履歴を表示", exact: true }).click();
  await expect(page.getByRole("tab", { name: "概要", exact: true })).toHaveAttribute("aria-selected", "true");
});

test("履歴の更新失敗後も続きが読めて条件変更では旧 cursor を使わない", async ({ page }, testInfo) => {
  const requests: URL[] = [];
  const extra = { ...historyItems[0], id: "hist-extra", question: "追加履歴" };
  let failRefresh = false;
  let releaseRefresh: (() => void) | undefined;
  const gate = new Promise<void>((resolve) => { releaseRefresh = resolve; });
  await page.route("**/api/nl2sql/history**", async (route) => {
    const url = new URL(route.request().url());
    requests.push(url);
    if (url.searchParams.get("cursor") === "next-1") return fulfillJson(route, { items: [extra], next_cursor: "next-2", total: 5 });
    if (!failRefresh) return fulfillJson(route, { items: historyItems, next_cursor: "next-1", total: 5 });
    await gate;
    await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: "履歴更新テストエラー" }) });
  });
  await page.goto("/history");
  await expect(historyRows(page)).toHaveCount(3);
  const more = page.getByRole("button", { name: "さらに読み込む", exact: true });
  failRefresh = true;
  await page.getByRole("button", { name: "表示を更新", exact: true }).click();
  try { await expect(more).toBeDisabled(); } finally { releaseRefresh?.(); }
  await expect(page.getByText(/履歴更新テストエラー/)).toBeVisible();
  await expect(more).toBeEnabled();
  await more.press("Enter");
  await expect(historyRows(page)).toHaveCount(4);
  await expect(more).toBeEnabled();
  await page.screenshot({ path: testInfo.outputPath("history-refresh-recovery.png") });
  await page.getByLabel("利用者評価フィルター").selectOption("unrated");
  await expect.poll(() => requests.at(-1)?.searchParams.get("rating")).toBe("unrated");
  await expect(more).toHaveCount(0);
  expect(requests.filter((url) => url.searchParams.has("cursor")).map((url) => url.searchParams.get("cursor"))).toEqual(["next-1"]);
});

// 各主要導線の最終状態で全テキスト・入力欄の字体継承を確認する。
test.afterEach(async ({ page }, testInfo) => {
  if (testInfo.status === "skipped") return;
  await expectLocalUiFonts(page);
});
