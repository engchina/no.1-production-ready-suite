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

const employeeOntologyGraph = {
  nodes: [
    {
      id: "employee-business",
      kind: "business_entity",
      business_name_ja: "従業員",
      technical_name: "ADMIN.EMPLOYEE",
      review_status: "approved",
      validation_status: "passed",
      aliases: ["社員"],
      physical_mappings: [
        {
          object_ref: {
            node_id: "employee-table",
            owner: "ADMIN",
            object_name: "EMPLOYEE",
            object_type: "table",
          },
        },
      ],
    },
    {
      id: "employee-table",
      kind: "table",
      business_name_ja: "従業員情報",
      technical_name: "ADMIN.EMPLOYEE",
      review_status: "approved",
      validation_status: "passed",
      physical_mappings: [
        {
          object_ref: {
            node_id: "employee-table",
            owner: "ADMIN",
            object_name: "EMPLOYEE",
            object_type: "table",
          },
        },
      ],
    },
  ],
  edges: [
    {
      id: "employee-maps-to",
      kind: "maps_to",
      source_node_id: "employee-business",
      target_node_id: "employee-table",
      relationship_name_ja: "物理マッピング",
      review_status: "approved",
      validation_status: "passed",
    },
  ],
};

const relationshipRows = Array.from({ length: 30 }, (_, index) => {
  const source = edges[index % edges.length];
  return {
    ...source,
    id: `relationship-row-${index + 1}`,
    relationship_name_ja: `${source.relationship_name_ja} ${index + 1}`,
  };
});

async function expectNoHorizontalScroll(page: Page) {
  const size = await page.evaluate(() => ({
    width: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  expect(size.scrollWidth).toBeLessThanOrEqual(size.width + 1);
}

async function expectQuestionActionLayout(page: Page, playground: Locator) {
  const input = playground.getByTestId("ontology-playground-question");
  const button = playground.getByTestId("ontology-playground-run");
  const [inputBox, buttonBox] = await Promise.all([input.boundingBox(), button.boundingBox()]);
  expect(inputBox).not.toBeNull();
  expect(buttonBox).not.toBeNull();
  expect(inputBox!.height).toBeGreaterThanOrEqual(43);
  expect(buttonBox!.height).toBeGreaterThanOrEqual(43);
  expect(Math.abs(inputBox!.height - buttonBox!.height)).toBeLessThanOrEqual(1);

  const viewportWidth = page.viewportSize()?.width ?? 0;
  if (viewportWidth >= 640) {
    expect(buttonBox!.x).toBeGreaterThan(inputBox!.x + inputBox!.width);
    expect(Math.abs(buttonBox!.y - inputBox!.y)).toBeLessThanOrEqual(1);
    return;
  }

  expect(buttonBox!.y).toBeGreaterThan(inputBox!.y + inputBox!.height);
  expect(Math.abs(buttonBox!.x - inputBox!.x)).toBeLessThanOrEqual(1);
  expect(buttonBox!.width).toBeGreaterThanOrEqual(inputBox!.width - 1);
}

type MockApiOptions = {
  ontologyGraph?: {
    nodes: unknown[];
    edges: unknown[];
  };
};

async function mockApi(page: Page, options: MockApiOptions = {}) {
  const ontologyGraph = options.ontologyGraph ?? {
    nodes,
    edges: relationshipRows,
  };

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
        nodes: ontologyGraph.nodes,
        edges: ontologyGraph.edges,
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
  await page.route("**/api/nl2sql/profiles/*/ontology-markdown", (route) =>
    fulfillJson(route, {
      draft_markdown: "",
      published_markdown: "",
      draft_revision: null,
      published_revision: null,
      draft_etag: "",
      published_at: null,
    })
  );
  await page.route("**/api/nl2sql/profiles/*/ontology-build-jobs**", (route) =>
    fulfillJson(route, { jobs: [] })
  );
}

test("グラフはカード表示 + 検索 + 詳細ノードの折畳ができる", async ({ page }, testInfo) => {
  await mockApi(page);
  await page.goto("/ontology-build?profile=default");

  const playground = page.getByRole("region", { name: "質問の Ontology 接地確認" });
  await playground.scrollIntoViewIfNeeded();
  await expect(
    playground.getByText("質問を入力すると、一致したノードと関係をグラフで強調表示します。")
  ).toBeVisible();
  await expect(playground.getByText("確認対象: 5 ノード / 30 関係")).toBeVisible();
  await expectQuestionActionLayout(page, playground);
  await expectNoHorizontalScroll(page);

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

test("同じ物理名の業務概念と物理表をカード上で区別できる", async ({ page }) => {
  await mockApi(page, { ontologyGraph: employeeOntologyGraph });
  await page.goto("/ontology-build?profile=default");

  const playground = page.getByRole("region", { name: "質問の Ontology 接地確認" });
  await playground.scrollIntoViewIfNeeded();

  const businessCard = playground.getByTestId("ontology-node-card-employee-business");
  await expect(businessCard).toBeVisible();
  await expect(businessCard).toHaveAttribute("data-ontology-node-kind", "business_entity");
  await expect(businessCard).toContainText("業務概念");
  await expect(businessCard).toContainText("従業員");
  await expect(businessCard).toContainText("対応表: ADMIN.EMPLOYEE");

  const tableCard = playground.getByTestId("ontology-node-card-employee-table");
  await expect(tableCard).toBeVisible();
  await expect(tableCard).toHaveAttribute("data-ontology-node-kind", "table");
  await expect(tableCard).toContainText("物理表");
  await expect(tableCard).toContainText("従業員情報");
  await expect(tableCard).toContainText("物理名: ADMIN.EMPLOYEE");

  const legend = playground.getByTestId("ontology-graph-legend");
  await expect(legend).toContainText("業務概念");
  await expect(legend).toContainText("物理表・ビュー");
  await expect(legend).toContainText("物理マッピング = 業務概念と物理表・ビューの対応");
  await expectNoHorizontalScroll(page);
});

test("質問を接地すると分類とグラフ強調が表示される", async ({ page }) => {
  await mockApi(page);
  await page.goto("/ontology-build?profile=default");

  const playground = page.getByRole("region", { name: "質問の Ontology 接地確認" });
  await playground.scrollIntoViewIfNeeded();
  await playground.getByTestId("ontology-playground-question").fill("顧客と注文の関係は?");
  await playground.getByTestId("ontology-playground-run").click();

  const result = playground.getByTestId("ontology-playground-result");
  await expect(result).toContainText("関係の一致");
  await expect(result).toContainText("注文する");
  await expect(playground.getByTestId("ontology-playground-ready-state")).toHaveCount(0);

  const metricCard = playground
    .locator(".react-flow__node", { hasText: "売上合計" })
    .locator("div")
    .first();
  await expect(metricCard).toHaveCSS("opacity", "0.35");
});

test("公開済み Ontology がない場合は準備手順を表示する", async ({ page }) => {
  await mockApi(page, { ontologyGraph: { nodes: [], edges: [] } });
  await page.goto("/ontology-build?profile=default");

  const playground = page.getByRole("region", { name: "質問の Ontology 接地確認" });
  await playground.scrollIntoViewIfNeeded();
  await expect(playground.getByText("公開済み Ontology がまだありません")).toBeVisible();
  await expect(
    playground.getByText("AI 構築の Markdown Draft を確認して公開すると、質問がどの業務モデルに接地するかをここで確認できます。")
  ).toBeVisible();
  await expect(playground.getByText("準備: AI 構築 → Markdown Draft 確認 → Ontology 公開")).toBeVisible();
  await expect(playground.getByTestId("ontology-playground-question")).toHaveCount(0);
  await expectNoHorizontalScroll(page);
});

test("Ontology グラフはデスクトップとモバイルで主要ノード・凡例を表示し横スクロールしない", async ({
  page,
}) => {
  await mockApi(page);
  await page.goto("/ontology-build?profile=default");

  const playground = page.getByRole("region", { name: "質問の Ontology 接地確認" });
  await playground.scrollIntoViewIfNeeded();
  await expect(playground.locator(".react-flow")).toBeVisible();
  await expect(playground.locator(".react-flow__node")).toHaveCount(3);
  await expect(playground.getByTestId("ontology-graph-legend")).toBeVisible();
  await expect(playground.locator(".react-flow__node", { hasText: "顧客" })).toBeVisible();
  await expect(playground.locator(".react-flow__node", { hasText: "注文" })).toBeVisible();
  await expectNoHorizontalScroll(page);
});
