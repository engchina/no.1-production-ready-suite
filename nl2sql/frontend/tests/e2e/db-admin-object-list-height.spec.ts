import { expect, test, type Page, type Route } from "@playwright/test";

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
  const items = Array.from({ length: 12 }, (_, index) => ({
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
}

for (const scenario of scenarios) {
  test(`${scenario.title}は 10 件分の高さで表示する`, async ({ page }) => {
    await mockObjectManagementApi(page, scenario);
    await page.goto(scenario.path);

    const list = page.getByTestId("db-admin-object-list");
    await expect(list.getByRole("listitem")).toHaveCount(12);

    const fit = await list.evaluate((node) => {
      const listBox = node.getBoundingClientRect();
      const rows = Array.from(node.querySelectorAll("button")).map((row) => row.getBoundingClientRect());
      return {
        listHeight: listBox.height,
        tenthInside: rows[9].bottom <= listBox.bottom + 1,
        eleventhBelow: rows[10].bottom > listBox.bottom + 1,
      };
    });

    expect(fit.listHeight).toBeGreaterThanOrEqual(622);
    expect(fit.tenthInside).toBe(true);
    expect(fit.eleventhBelow).toBe(true);
  });
}
