import { expect, test, type Locator, type Page, type Route } from "@playwright/test";

async function fulfillJson(route: Route, data: unknown) {
  await route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ data }),
  });
}

const scenarios = [
  {
    title: "テーブル一覧",
    path: "/table-management",
    apiPath: "**/api/nl2sql/db-admin/tables",
    prefix: "TABLE",
    objectType: "table",
  },
  {
    title: "ビュー一覧",
    path: "/view-management",
    apiPath: "**/api/nl2sql/db-admin/views",
    prefix: "VIEW",
    objectType: "view",
  },
] as const;

async function mockObjectManagementApi(page: Page, scenario: (typeof scenarios)[number]) {
  const items = Array.from({ length: 30 }, (_, index) => ({
    name: `${scenario.prefix}_${String(index + 1).padStart(2, "0")}`,
    owner: "APP",
    object_type: scenario.objectType,
    row_count: null,
    comment: "",
  }));

  await page.route("**/api/schema/catalog", (route) =>
    fulfillJson(route, {
      refreshed_at: "2026-06-21T10:00:00.000Z",
      tables: [],
    })
  );
  await page.route(scenario.apiPath, (route) =>
    fulfillJson(route, {
      runtime: "deterministic",
      items,
      warnings: [],
    })
  );
  await page.route("**/api/nl2sql/db-admin/tables/*", (route) =>
    fulfillJson(route, {
      name: "TABLE_01",
      owner: "APP",
      object_type: "table",
      row_count: null,
      comment: "",
      columns: [],
      ddl: 'CREATE TABLE "TABLE_01" ("ID" NUMBER)',
      warnings: [],
    })
  );
}

async function expectSingleLine(locator: Locator) {
  const lineCount = await locator.evaluate((node) => {
    const range = document.createRange();
    range.selectNodeContents(node);
    const rects = Array.from(range.getClientRects()).filter((rect) => rect.width > 0 && rect.height > 0);
    range.detach();
    return rects.length;
  });
  expect(lineCount).toBeLessThanOrEqual(1);
}

for (const scenario of scenarios) {
  test(
    scenario.objectType === "table"
      ? `${scenario.title}はグリッドの高さを上限内に収める`
      : `${scenario.title}は 10 件分の高さで表示する`,
    async ({ page }) => {
    await mockObjectManagementApi(page, scenario);
    await page.goto(scenario.path);

    const list = page.getByTestId("db-admin-object-list");
    if (scenario.objectType === "table") {
      await expect(page.getByTestId("table-management-grid").locator("tbody tr")).toHaveCount(30);
    } else {
      await expect(list.getByRole("listitem")).toHaveCount(30);
    }

    const fit = await list.evaluate((node) => {
      const listBox = node.getBoundingClientRect();
      const rowSelector = node.querySelector("tbody tr") ? "tbody tr" : "button";
      const rows = Array.from(node.querySelectorAll(rowSelector)).map((row) => row.getBoundingClientRect());
      return {
        listHeight: listBox.height,
        firstInside: rows[0].top >= listBox.top - 1 && rows[0].bottom <= listBox.bottom + 1,
        tenthInside: rows[9].bottom <= listBox.bottom + 1,
        eleventhBelow: rows[10].bottom > listBox.bottom + 1,
        lastBelow: rows[rows.length - 1].bottom > listBox.bottom + 1,
      };
    });

    if (scenario.objectType === "table") {
      expect(fit.listHeight).toBeGreaterThanOrEqual(320);
      expect(fit.listHeight).toBeLessThanOrEqual(680);
      expect(fit.firstInside).toBe(true);
      expect(fit.lastBelow).toBe(true);
    } else {
      expect(fit.listHeight).toBeGreaterThanOrEqual(622);
      expect(fit.tenthInside).toBe(true);
      expect(fit.eleventhBelow).toBe(true);
    }
    }
  );
}

test("テーブル管理は 150% zoom 相当でもタブ・列名・操作ボタンを折り返さない", async ({ page }) => {
  await page.setViewportSize({ width: 1365, height: 900 });
  await mockObjectManagementApi(page, scenarios[0]);
  await page.goto("/table-management");

  await expect(page.getByRole("tab", { name: "テーブル一覧と詳細" })).toHaveAttribute("aria-selected", "true");
  await expectSingleLine(page.getByRole("tab", { name: "テーブル一覧と詳細" }).locator("span").last());
  await expectSingleLine(page.getByRole("tab", { name: "テーブル作成" }).locator("span").last());
  await expectSingleLine(page.getByRole("tab", { name: "Excel/CSV 取込(新規テーブル)" }).locator("span").last());
  await expectSingleLine(page.getByRole("tab", { name: "列情報" }).locator("span").last());
  await expectSingleLine(page.getByRole("tab", { name: "DDL" }).locator("span").last());

  const grid = page.getByTestId("table-management-grid");
  await expect(grid.locator("tbody tr")).toHaveCount(30);
  await expect(grid.getByRole("columnheader", { name: /コメント/ })).toHaveCount(0);
  await expectSingleLine(grid.getByRole("columnheader", { name: /所有者/ }).locator("span").first());
  await expectSingleLine(grid.getByRole("button", { name: "詳細" }).first().locator("span").first());
  await expectSingleLine(grid.getByRole("button", { name: "削除" }).first().locator("span").first());

  const scroll = await page.getByTestId("db-admin-object-list").evaluate((node) => ({
    internalWidthStable: node.scrollWidth >= node.clientWidth,
    pageHorizontal:
      document.documentElement.scrollWidth > document.documentElement.clientWidth + 1 ||
      document.body.scrollWidth > document.body.clientWidth + 1,
  }));
  expect(scroll.internalWidthStable).toBe(true);
  expect(scroll.pageHorizontal).toBe(false);
});
