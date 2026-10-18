import { expect, test, type Locator, type Page, type Route } from "./_helpers/test";
import { mockDatabaseGateReady } from "./_helpers/database-gate";

test.beforeEach(async ({ page }) => mockDatabaseGateReady(page));

async function fulfillJson(route: Route, data: unknown) {
  await route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ data }),
  });
}

async function mockDataManagementApi(page: Page) {
  await page.route("**/api/nl2sql/db-admin/objects?*", (route) =>
    fulfillJson(route, {
      runtime: "oracle",
      owner: "APP",
      items: [
        {
          name: "DENPYO_FILES",
          owner: "APP",
          object_type: "table",
          row_count: 78,
          comment: "data preview split pane",
        },
      ],
      total: 1,
      table_count: 1,
      view_count: 0,
      next_cursor: null,
      refreshed_at: "2026-07-14T22:48:00.000Z",
      catalog_version: 1,
      warnings: [],
    })
  );
  await page.route("**/api/nl2sql/db-admin/preview-data", (route) =>
    fulfillJson(route, {
      runtime: "oracle",
      sql: 'SELECT * FROM "DENPYO_FILES" FETCH FIRST 100 ROWS ONLY',
      results: {
        columns: ["ANALYZED_AT", "ANALYSIS_RESULT", "ANALYSIS_RESULT_1", "ANALYSIS_RESULT_2"],
        rows: [
          {
            ANALYZED_AT: "2026-07-14T22:48:00+09:00",
            ANALYSIS_RESULT: '{"category_guess":"receipt","header_columns":["伝票番号"]}',
            ANALYSIS_RESULT_1: "長い抽出結果を表示しても中央の分割線へ重ならない",
            ANALYSIS_RESULT_2: "verified",
          },
        ],
        total: 1,
      },
      warnings: [],
    })
  );
}

async function dragDivider(page: Page, divider: Locator, deltaX: number) {
  const box = await divider.boundingBox();
  expect(box).not.toBeNull();
  const centerX = box!.x + box!.width / 2;
  const centerY = box!.y + box!.height / 2;
  await page.mouse.move(centerX, centerY);
  await page.mouse.down();
  await page.mouse.move(centerX + deltaX, centerY, { steps: 8 });
  await page.mouse.up();
}

// テーブルの管理の一覧 + 詳細は FixedSplitPane から ListDetailLayout に移した（#2281。table-management-list-detail.spec.ts）。
// FixedSplitPane は C 型（入力 | 結果）と比較だけに使う。

test("データ管理の分割線は左右の内容幅を保ち、狭い画面では単列化する", { tag: "@responsive" }, async ({ page }) => {
  await page.setViewportSize({ width: 2048, height: 1000 });
  await page.addInitScript(() => {
    window.localStorage.removeItem("production-ready-nl2sql.fixedSplitPane.data-management-preview");
  });
  await mockDataManagementApi(page);
  await page.goto("/data-management");

  const pane = page.getByTestId("fixed-split-pane-data-management-preview");
  const left = page.getByTestId("fixed-split-pane-data-management-preview-left");
  const divider = page.getByTestId("fixed-split-pane-data-management-preview-divider");
  const right = page.getByTestId("fixed-split-pane-data-management-preview-right");
  await expect(pane).toHaveAttribute("data-split-layout", "split");
  await expect(divider).toBeVisible();
  await page.getByRole("button", { name: "DENPYO_FILES を選択" }).click();
  await right.getByRole("button", { name: "データを表示", exact: true }).click();
  await expect(page.getByTestId("query-results-table")).toBeVisible();

  for (const deltaX of [-4_000, 8_000]) {
    await dragDivider(page, divider, deltaX);
    const leftBox = await left.boundingBox();
    const dividerBox = await divider.boundingBox();
    const rightBox = await right.boundingBox();
    expect(leftBox).not.toBeNull();
    expect(dividerBox).not.toBeNull();
    expect(rightBox).not.toBeNull();
    expect(leftBox!.width).toBeGreaterThanOrEqual(519);
    expect(rightBox!.width).toBeGreaterThanOrEqual(559);
    expect(leftBox!.x + leftBox!.width).toBeLessThanOrEqual(dividerBox!.x + 1);
    expect(dividerBox!.x + dividerBox!.width).toBeLessThanOrEqual(rightBox!.x + 1);
    expect(await documentHorizontalScroll(page)).toBe(false);
  }

  await page.setViewportSize({ width: 1280, height: 900 });
  await expect(pane).toHaveAttribute("data-split-layout", "stacked");
  await expect(divider).toBeHidden();
  const stackedLeft = await left.boundingBox();
  const stackedRight = await right.boundingBox();
  expect(stackedLeft).not.toBeNull();
  expect(stackedRight).not.toBeNull();
  expect(stackedRight!.y).toBeGreaterThan(stackedLeft!.y);
  expect(await documentHorizontalScroll(page)).toBe(false);
});

async function documentHorizontalScroll(page: Page) {
  return page.evaluate(
    () =>
      document.documentElement.scrollWidth > document.documentElement.clientWidth + 1 ||
      document.body.scrollWidth > document.body.clientWidth + 1
  );
}
