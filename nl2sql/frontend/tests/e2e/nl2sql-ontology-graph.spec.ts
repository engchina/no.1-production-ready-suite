import { expect, test, type Locator, type Page, type Route, type TestInfo } from "@playwright/test";
import { mockDatabaseGateReady } from "./_helpers/database-gate";

test.beforeEach(async ({ page }) => mockDatabaseGateReady(page));

async function fulfillJson(route: Route, data: unknown) {
  await route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ data }),
  });
}

const profile = {
  id: "default",
  name: "既定プロファイル",
  category: "既定",
  description: "",
  allowed_tables: ["ORDERS"],
  allowed_views: [],
  glossary: {},
  sql_rules: [],
  default_row_limit: 100,
  safety_policy: "select_only",
  few_shot_examples: [],
  select_ai_config: null,
  archived: false,
};

const nodes = [
  {
    id: "e1",
    kind: "business_entity",
    business_name_ja: "顧客",
    technical_name: "APP.CUSTOMERS",
    review_status: "approved",
    validation_status: "passed",
    aliases: ["得意先"],
  },
  {
    id: "e2",
    kind: "business_entity",
    business_name_ja: "注文",
    technical_name: "APP.ORDERS",
    review_status: "approved",
  },
  {
    id: "m1",
    kind: "metric",
    business_name_ja: "売上合計",
    technical_name: "SUM(AMOUNT)",
    review_status: "approved",
  },
  {
    id: "c1",
    kind: "column",
    business_name_ja: "顧客ID",
    technical_name: "APP.CUSTOMERS.ID",
    review_status: "approved",
  },
  {
    id: "c2",
    kind: "column",
    business_name_ja: "注文金額",
    technical_name: "APP.ORDERS.AMOUNT",
    review_status: "approved",
  },
];
const edges = [
  {
    id: "r1",
    kind: "business_relationship",
    source_node_id: "e1",
    target_node_id: "e2",
    relationship_name_ja: "注文する",
    cardinality: "one_to_many",
    review_status: "approved",
  },
  {
    id: "r4",
    kind: "maps_to",
    source_node_id: "m1",
    target_node_id: "e2",
    relationship_name_ja: "集計対象",
    review_status: "approved",
  },
  {
    id: "r5",
    kind: "maps_to",
    source_node_id: "c1",
    target_node_id: "e1",
    relationship_name_ja: "列",
    review_status: "approved",
  },
  {
    id: "r6",
    kind: "maps_to",
    source_node_id: "c2",
    target_node_id: "e2",
    relationship_name_ja: "列",
    review_status: "approved",
  },
];

const relationshipRows = Array.from({ length: 30 }, (_, index) => {
  const source = edges[index % edges.length];
  return {
    ...source,
    id: `relationship-row-${index + 1}`,
    relationship_name_ja: `${source.relationship_name_ja} ${index + 1}`,
  };
});

function expectedInformationRows(testInfo: TestInfo) {
  return testInfo.project.name === "mobile-375" ? 5 : 8;
}

async function expectInformationTableRowLimit(
  list: Locator,
  rowSelector: string,
  visibleRows: number
) {
  const fit = await list.evaluate(
    (node, { rowSelector: selector }) => {
      const listBox = node.getBoundingClientRect();
      const rows = Array.from(node.querySelectorAll(selector)).map((row) =>
        row.getBoundingClientRect()
      );
      const header = node.querySelector("thead");
      const computed = window.getComputedStyle(node);
      const rootFontSize = Number.parseFloat(
        window.getComputedStyle(document.documentElement).fontSize
      );
      return {
        listHeight: listBox.height,
        maxHeight: Number.parseFloat(computed.maxHeight),
        rootFontSize,
        headerPosition: header ? window.getComputedStyle(header).position : "",
        rowCount: rows.length,
        scrollHeight: node.scrollHeight,
        clientHeight: node.clientHeight,
      };
    },
    { rowSelector }
  );
  const expectedMaxHeight = fit.rootFontSize * (2.5 + 3.5 * visibleRows);
  expect(fit.maxHeight).toBeGreaterThanOrEqual(expectedMaxHeight - 2);
  expect(fit.maxHeight).toBeLessThanOrEqual(expectedMaxHeight + 2);
  expect(Math.abs(fit.listHeight - fit.maxHeight)).toBeLessThanOrEqual(2);
  expect(fit.rowCount).toBeGreaterThan(visibleRows);
  expect(fit.scrollHeight).toBeGreaterThan(fit.clientHeight);
  expect(fit.headerPosition).toBe("sticky");
}

async function expectNoHorizontalScroll(page: Page) {
  const size = await page.evaluate(() => ({
    width: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  expect(size.scrollWidth).toBeLessThanOrEqual(size.width + 1);
}

async function mockApi(page: Page) {
  await page.route("**/api/schema/catalog", (route) =>
    fulfillJson(route, { refreshed_at: "2026-07-12T00:00:00Z", tables: [] })
  );
  await page.route("**/api/nl2sql/profiles", (route) => fulfillJson(route, [profile]));
  await page.route("**/api/nl2sql/profiles/search?*", (route) =>
    fulfillJson(route, {
      items: [
        {
          id: "default",
          name: "既定プロファイル",
          category: "既定",
          description: "",
          archived: false,
          allowed_table_count: 1,
          allowed_view_count: 0,
          glossary_count: 0,
          few_shot_count: 0,
          version: 1,
          etag: "e",
          updated_at: "2026-07-12T00:00:00Z",
        },
      ],
      next_cursor: null,
      total: 1,
      change_token: 1,
    })
  );
  await page.route(/\/api\/nl2sql\/profiles\/[^/?]+$/, (route) =>
    fulfillJson(route, profile)
  );
  await page.route("**/api/nl2sql/profiles/*/ontology-view", (route) =>
    fulfillJson(route, {
      materialized: true,
      stale: false,
      profile_ontology_view: {
        id: "v",
        profile_id: "default",
        ontology_revision_id: "rev1",
        etag: "ve",
      },
      ontology_graph: {
        revision: {
          id: "rev1",
          version: 1,
          status: "published",
          schema_fingerprint: "fp",
          etag: "re",
        },
        nodes,
        edges: relationshipRows,
      },
    })
  );
  await page.route("**/api/nl2sql/profiles/*/ontology-view/mermaid", (route) =>
    fulfillJson(route, { mermaid: "erDiagram" })
  );
  await page.route("**/api/nl2sql/ontology/revisions", (route) =>
    fulfillJson(route, { revisions: [], active_revision_id: "" })
  );
  await page.route("**/api/nl2sql/profiles/*/ontology-proposals", (route) =>
    fulfillJson(route, { proposals: [] })
  );
  await page.route("**/api/nl2sql/profiles/*/ontology-build-jobs**", (route) =>
    fulfillJson(route, { jobs: [] })
  );
  await page.route("**/api/nl2sql/ontology-templates", (route) =>
    fulfillJson(route, { templates: [] })
  );
}

test("グラフはカード表示 + 検索 + 詳細ノードの折畳ができる", async ({ page }, testInfo) => {
  await mockApi(page);
  await page.goto("/ontology-build?profile=default");

  const playground = page.getByRole("region", { name: "質問プレイグラウンド(決定論)" });
  await playground.scrollIntoViewIfNeeded();

  // カードノード: 業務名 + 技術名の 2 段表示
  const customer = playground.locator(".react-flow__node", { hasText: "顧客" }).first();
  await expect(customer).toBeVisible();
  await expect(customer).toContainText("APP.CUSTOMERS");

  // 既定では列・列挙値ノード(2 件)は畳まれる
  await expect(playground.locator(".react-flow__node")).toHaveCount(3);
  await playground.getByTestId("ontology-graph-details-toggle").check();
  await expect(playground.locator(".react-flow__node")).toHaveCount(5);
  await playground.getByTestId("ontology-graph-details-toggle").uncheck();

  // 検索は一致ノードを強調し、非一致を減光する(opacity)
  await playground.getByTestId("ontology-graph-search").fill("顧客");
  const orderCard = playground
    .locator(".react-flow__node", { hasText: "売上合計" })
    .locator("div")
    .first();
  await expect(orderCard).toHaveCSS("opacity", "0.35");
  await playground.getByTestId("ontology-graph-search").fill("");
  await expect(orderCard).toHaveCSS("opacity", "1");

  await playground.screenshot({
    path: testInfo.outputPath("ontology-graph-canvas.png"),
  });
});

test("Ontology 関連一覧はデスクトップ8行・モバイル5行相当の高さで内部スクロールする", async ({
  page,
}, testInfo) => {
  await mockApi(page);
  await page.goto("/ontology-build?profile=default");

  if (testInfo.project.name === "mobile-375") {
    const cardList = page.getByTestId("ontology-relationship-card-list");
    await expect(cardList).toBeVisible();
    expect(await cardList.locator("button").count()).toBeGreaterThan(expectedInformationRows(testInfo));
    const state = await cardList.evaluate((node) => {
      const computed = window.getComputedStyle(node);
      const rootFontSize = Number.parseFloat(
        window.getComputedStyle(document.documentElement).fontSize
      );
      return {
        listHeight: node.getBoundingClientRect().height,
        maxHeight: Number.parseFloat(computed.maxHeight),
        rootFontSize,
        scrollHeight: node.scrollHeight,
        clientHeight: node.clientHeight,
      };
    });
    expect(state.maxHeight).toBeGreaterThanOrEqual(state.rootFontSize * 17.5 - 2);
    expect(state.maxHeight).toBeLessThanOrEqual(state.rootFontSize * 17.5 + 2);
    expect(Math.abs(state.listHeight - state.maxHeight)).toBeLessThanOrEqual(2);
    expect(state.scrollHeight).toBeGreaterThan(state.clientHeight);
    await expectNoHorizontalScroll(page);
    return;
  }

  const table = page.getByTestId("ontology-relationship-table-scroll-region");
  await expect(table).toBeVisible();
  expect(await table.locator("tbody tr").count()).toBeGreaterThan(expectedInformationRows(testInfo));
  await expectInformationTableRowLimit(table, "tbody tr", expectedInformationRows(testInfo));
  await expectNoHorizontalScroll(page);
});
