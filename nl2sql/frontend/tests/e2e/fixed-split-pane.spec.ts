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

async function mockTableManagementApi(page: Page) {
  await page.route("**/api/schema/catalog", (route) =>
    fulfillJson(route, {
      refreshed_at: "2026-06-21T10:00:00.000Z",
      tables: [],
    })
  );
  await page.route("**/api/schema/refresh", (route) =>
    fulfillJson(route, {
      refreshed_at: "2026-06-21T10:00:00.000Z",
      tables: [],
    })
  );
  await page.route("**/api/nl2sql/db-admin/tables", (route) =>
    fulfillJson(route, {
      runtime: "deterministic",
      items: [
        { name: "TABLE_01", owner: "APP", object_type: "table", row_count: 10, comment: "split pane table" },
      ],
      warnings: [],
    })
  );
  await page.route("**/api/nl2sql/db-admin/tables/*", (route) =>
    fulfillJson(route, {
      name: "TABLE_01",
      owner: "APP",
      object_type: "table",
      row_count: 10,
      comment: "split pane table",
      columns: [],
      ddl: 'CREATE TABLE "TABLE_01" ("ID" NUMBER)',
      warnings: [],
    })
  );
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

async function paneWidths(page: Page) {
  const left = await page.getByTestId("fixed-split-pane-table-management-list-left").boundingBox();
  const right = await page.getByTestId("fixed-split-pane-table-management-list-right").boundingBox();
  expect(left).not.toBeNull();
  expect(right).not.toBeNull();
  return { left: left!.width, right: right!.width };
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

async function leftFraction(pane: Locator) {
  return Number(await pane.getAttribute("data-split-left-fraction"));
}

async function expectDividerReservedTrack(page: Page, minPaneWidth = 320) {
  const leftLocator = page.getByTestId("fixed-split-pane-table-management-list-left");
  const dividerLocator = page.getByTestId("fixed-split-pane-table-management-list-divider");
  const rightLocator = page.getByTestId("fixed-split-pane-table-management-list-right");
  const [left, divider, right] = await Promise.all([
    leftLocator.boundingBox(),
    dividerLocator.boundingBox(),
    rightLocator.boundingBox(),
  ]);
  expect(left).not.toBeNull();
  expect(divider).not.toBeNull();
  expect(right).not.toBeNull();
  await expect(leftLocator).toHaveAttribute("data-split-pane-side", "left");
  await expect(rightLocator).toHaveAttribute("data-split-pane-side", "right");
  await expect(leftLocator).toHaveCSS("overflow-x", "clip");
  await expect(rightLocator).toHaveCSS("overflow-x", "clip");
  expect(divider!.width).toBeCloseTo(14, 0);
  expect(left!.width).toBeGreaterThanOrEqual(minPaneWidth - 1);
  expect(right!.width).toBeGreaterThanOrEqual(minPaneWidth - 1);
  expect(left!.x + left!.width).toBeLessThanOrEqual(divider!.x + 1);
  expect(divider!.x + divider!.width).toBeLessThanOrEqual(right!.x + 1);
}

function expectNearRatio(actual: number, expected: number) {
  expect(Math.abs(actual - expected)).toBeLessThan(0.12);
}

test("固定分割 slide は三档切替・ページ別保存・モバイル単列を満たす", async ({ page }) => {
  await page.addInitScript(() => {
    if (!window.sessionStorage.getItem("fixed-split-pane-test-cleared")) {
      window.localStorage.removeItem("production-ready-nl2sql.fixedSplitPane.table-management-list");
      window.sessionStorage.setItem("fixed-split-pane-test-cleared", "true");
    }
  });
  await mockTableManagementApi(page);
  await page.goto("/table-management");

  const pane = page.getByTestId("fixed-split-pane-table-management-list");
  const divider = page.getByTestId("fixed-split-pane-table-management-list-divider");
  await expect(pane).toBeVisible();

  const viewport = page.viewportSize();
  const isDesktop = (viewport?.width ?? 0) >= 1280;

  if (!isDesktop) {
    await expect(pane).toHaveAttribute("data-split-layout", "stacked");
    await expect(divider).toBeHidden();
    const left = await page.getByTestId("fixed-split-pane-table-management-list-left").boundingBox();
    const right = await page.getByTestId("fixed-split-pane-table-management-list-right").boundingBox();
    expect(left).not.toBeNull();
    expect(right).not.toBeNull();
    expect(Math.abs(left!.width - right!.width)).toBeLessThan(2);
    expect(right!.y).toBeGreaterThan(left!.y);
    expect(await documentHorizontalScroll(page)).toBe(false);
    return;
  }

  await expect(divider).toBeVisible();
  await expect(divider).toHaveAttribute("role", "separator");
  await expect(divider).toHaveAttribute("aria-label", "左右ペインの表示比率");
  await expect(pane).toHaveAttribute("data-split-ratio", "right-wide");

  let widths = await paneWidths(page);
  expectNearRatio(widths.right / widths.left, 1.618);

  await dragDivider(page, divider, 260);
  const draggedFraction = await leftFraction(pane);
  expect(draggedFraction).toBeGreaterThan(0.58);
  widths = await paneWidths(page);
  expect(widths.left).toBeGreaterThan(widths.right);

  await page.reload();
  await expect(pane).toBeVisible();
  await expect.poll(() => leftFraction(pane)).toBeGreaterThan(0.58);
  expect(Math.abs((await leftFraction(pane)) - draggedFraction)).toBeLessThan(0.02);

  await divider.dblclick();
  await expect(pane).toHaveAttribute("data-split-ratio", "right-wide");
  widths = await paneWidths(page);
  expectNearRatio(widths.right / widths.left, 1.618);

  await divider.press("Home");
  await expect(pane).toHaveAttribute("data-split-ratio", "equal");
  const equalFraction = await leftFraction(pane);
  await divider.press("ArrowRight");
  const arrowRightFraction = await leftFraction(pane);
  expect(arrowRightFraction).toBeGreaterThan(equalFraction);
  await divider.press("Shift+ArrowLeft");
  const shiftArrowLeftFraction = await leftFraction(pane);
  expect(equalFraction - shiftArrowLeftFraction).toBeGreaterThan(0.02);

  await divider.press("Home");
  await expect(pane).toHaveAttribute("data-split-ratio", "equal");

  await divider.dblclick();
  await expect(pane).toHaveAttribute("data-split-ratio", "left-wide");
  widths = await paneWidths(page);
  expectNearRatio(widths.left / widths.right, 1.618);

  await divider.dblclick();
  await expect(pane).toHaveAttribute("data-split-ratio", "right-wide");
  widths = await paneWidths(page);
  expectNearRatio(widths.right / widths.left, 1.618);

  await divider.dblclick();
  await expect(pane).toHaveAttribute("data-split-ratio", "equal");

  await divider.press("Home");
  await expect(pane).toHaveAttribute("data-split-ratio", "equal");

  await divider.press("Space");
  await expect(pane).toHaveAttribute("data-split-ratio", "left-wide");
  await page.reload();
  await expect(pane).toHaveAttribute("data-split-ratio", "left-wide");

  await dragDivider(page, divider, -2_000);
  await expectDividerReservedTrack(page);
  await dragDivider(page, divider, 4_000);
  await expectDividerReservedTrack(page);
  expect(await documentHorizontalScroll(page)).toBe(false);
});

test("データ管理の分割線は左右の内容幅を保ち、狭い画面では単列化する", async ({ page }) => {
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
  await page.getByRole("button", { name: "DENPYO_FILES のデータを表示" }).click();
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
