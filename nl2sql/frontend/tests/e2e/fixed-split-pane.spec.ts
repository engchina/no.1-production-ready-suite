import { expect, test, type Locator, type Page, type Route } from "@playwright/test";

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
  await expect(pane).toHaveAttribute("data-split-ratio", "equal");

  let widths = await paneWidths(page);
  expectNearRatio(widths.right / widths.left, 1);

  await dragDivider(page, divider, 160);
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
  expect(await documentHorizontalScroll(page)).toBe(false);
});

async function documentHorizontalScroll(page: Page) {
  return page.evaluate(
    () =>
      document.documentElement.scrollWidth > document.documentElement.clientWidth + 1 ||
      document.body.scrollWidth > document.body.clientWidth + 1
  );
}
