import { expect, test, type Page, type Route } from "@playwright/test";

async function fulfillJson(route: Route, data: unknown) {
  await route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ data }),
  });
}

const schemaCatalog = {
  refreshed_at: "2026-06-21T10:00:00.000Z",
  tables: [
    ...Array.from({ length: 24 }, (_, index) => {
      const count = String(index + 1).padStart(2, "0");
      const tableType = index % 2 === 0 ? "TABLE" : "VIEW";
      return {
        table_name: `${tableType}_${count}`,
        logical_name: `論理名_${count}`,
        owner: "APP",
        table_type: tableType,
        comment: `コメント_${count}`,
        row_count: null,
        columns: [],
        constraints: [],
      };
    }),
    {
      table_name: "SYS$AUDIT",
      logical_name: "システム監査",
      owner: "SYS",
      table_type: "TABLE",
      comment: "system table",
      row_count: null,
      columns: [],
      constraints: [],
    },
    {
      table_name: "V_$SESSION",
      logical_name: "システムセッション",
      owner: "SYS",
      table_type: "VIEW",
      comment: "system view",
      row_count: null,
      columns: [],
      constraints: [],
    },
  ],
};

const profiles = [
  {
    id: "default",
    name: "既定プロファイル",
    category: "既定プロファイル",
    description: "許可表の表示確認",
    allowed_tables: ["TABLE_01", "VIEW_02"],
    glossary: {},
    sql_rules: ["SELECT のみ"],
    default_row_limit: 100,
    safety_policy: "select_only",
    few_shot_examples: [],
    archived: false,
  },
];

async function mockProfileApi(page: Page) {
  await page.route("**/api/schema/catalog", (route) => fulfillJson(route, schemaCatalog));
  await page.route("**/api/nl2sql/profiles", (route) => fulfillJson(route, profiles));
}

test("業務プロファイルの許可表は名前だけを固定高で表示する", async ({ page }) => {
  await mockProfileApi(page);
  await page.goto("/profiles");

  const list = page.getByTestId("profile-allowed-table-list");
  await expect(list.getByText("TABLE_01", { exact: true })).toBeVisible();
  await expect(list.getByText("VIEW_02", { exact: true })).toBeVisible();
  await expect(list.getByText("SYS$AUDIT", { exact: true })).toHaveCount(0);
  await expect(list.getByText("V_$SESSION", { exact: true })).toHaveCount(0);
  await expect(page.getByText("論理名_01", { exact: true })).toHaveCount(0);
  await expect(page.getByText("コメント_01", { exact: true })).toHaveCount(0);

  const fit = await list.evaluate((node) => {
    const listBox = node.getBoundingClientRect();
    const rows = Array.from(node.querySelectorAll("label")).map((row) => row.getBoundingClientRect());
    const columnCount = getComputedStyle(node).gridTemplateColumns.split(" ").filter(Boolean).length;
    const lastVisibleIndex = columnCount > 1 ? 19 : 9;
    const firstOverflowIndex = columnCount > 1 ? 20 : 10;

    return {
      listHeight: listBox.height,
      lastVisibleInside: rows[lastVisibleIndex].bottom <= listBox.bottom + 1,
      firstOverflowBelow: rows[firstOverflowIndex].bottom > listBox.bottom + 1,
      scrollable: node.scrollHeight > node.clientHeight,
    };
  });

  expect(fit.listHeight).toBeGreaterThanOrEqual(440);
  expect(fit.listHeight).toBeLessThanOrEqual(460);
  expect(fit.lastVisibleInside).toBe(true);
  expect(fit.firstOverflowBelow).toBe(true);
  expect(fit.scrollable).toBe(true);
});
