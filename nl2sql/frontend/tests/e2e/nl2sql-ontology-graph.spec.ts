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

const erDetailOntologyGraph = {
  nodes: [
    {
      id: "employee-business",
      kind: "business_entity",
      business_name_ja: "従業員",
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
    {
      id: "employee-table",
      kind: "table",
      business_name_ja: "従業員情報",
      technical_name: "ADMIN.EMPLOYEE",
      review_status: "approved",
      validation_status: "passed",
      metadata: {
        owner: "ADMIN",
        object_name: "EMPLOYEE",
        object_type: "TABLE",
      },
    },
    {
      id: "department-table",
      kind: "table",
      business_name_ja: "部署情報",
      technical_name: "ADMIN.DEPARTMENT",
      review_status: "approved",
      validation_status: "passed",
      metadata: {
        owner: "ADMIN",
        object_name: "DEPARTMENT",
        object_type: "TABLE",
      },
    },
    {
      id: "employee-id",
      kind: "column",
      business_name_ja: "従業員ID",
      technical_name: "ADMIN.EMPLOYEE.EMPLOYEE_ID",
      review_status: "approved",
      metadata: {
        owner: "ADMIN",
        object_name: "EMPLOYEE",
        column_name: "EMPLOYEE_ID",
        data_type: "NUMBER",
        ordinal: 1,
        primary_key: true,
      },
    },
    {
      id: "employee-department-id",
      kind: "column",
      business_name_ja: "所属部署ID",
      technical_name: "ADMIN.EMPLOYEE.DEPARTMENT_ID",
      review_status: "approved",
      metadata: {
        owner: "ADMIN",
        object_name: "EMPLOYEE",
        column_name: "DEPARTMENT_ID",
        data_type: "NUMBER",
        ordinal: 2,
      },
    },
    {
      id: "employee-name",
      kind: "column",
      business_name_ja: "従業員氏名",
      technical_name: "ADMIN.EMPLOYEE.EMPLOYEE_NAME",
      description_ja: "従業員の氏名。",
      review_status: "approved",
      metadata: {
        owner: "ADMIN",
        object_name: "EMPLOYEE",
        column_name: "EMPLOYEE_NAME",
        data_type: "VARCHAR2",
        ordinal: 3,
      },
    },
    {
      id: "employee-hire-date",
      kind: "column",
      business_name_ja: "入社日",
      technical_name: "ADMIN.EMPLOYEE.HIRE_DATE",
      review_status: "approved",
      metadata: {
        owner: "ADMIN",
        object_name: "EMPLOYEE",
        column_name: "HIRE_DATE",
        data_type: "DATE",
        ordinal: 4,
      },
    },
    {
      id: "department-id",
      kind: "column",
      business_name_ja: "部署ID",
      technical_name: "ADMIN.DEPARTMENT.DEPARTMENT_ID",
      review_status: "approved",
      metadata: {
        owner: "ADMIN",
        object_name: "DEPARTMENT",
        column_name: "DEPARTMENT_ID",
        data_type: "NUMBER",
        ordinal: 1,
        primary_key: true,
      },
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
    {
      id: "employee-department-fk",
      kind: "foreign_key",
      source_node_id: "employee-table",
      target_node_id: "department-table",
      relationship_name_ja: "所属部署を参照",
      cardinality: "many_to_one",
      review_status: "approved",
      validation_status: "passed",
      join_conditions: [
        {
          left: {
            owner: "ADMIN",
            object_name: "EMPLOYEE",
            column_name: "DEPARTMENT_ID",
          },
          right: {
            owner: "ADMIN",
            object_name: "DEPARTMENT",
            column_name: "DEPARTMENT_ID",
          },
          operator: "=",
          ordinal: 1,
        },
      ],
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

function hasVisibleBoxShadow(value: string) {
  if (value === "none") return false;
  return value
    .replace(/rgba\(0, 0, 0, 0\) 0px 0px 0px 0px/g, "")
    .replace(/,\s*/g, "")
    .trim().length > 0;
}

async function openGraphIfCollapsed(page: Page, playground: Locator) {
  const viewportWidth = page.viewportSize()?.width ?? 0;
  if (viewportWidth >= 1280) return;
  const toggle = playground.getByRole("button", { name: "グラフを表示" });
  if (await toggle.isVisible()) {
    await toggle.click();
  }
}

async function expectQuestionActionLayoutWithClear(page: Page, playground: Locator) {
  const input = playground.getByTestId("ontology-playground-question");
  const runButton = playground.getByTestId("ontology-playground-run");
  const clearButton = playground.getByTestId("ontology-playground-clear");
  const [inputBox, runBox, clearBox] = await Promise.all([
    input.boundingBox(),
    runButton.boundingBox(),
    clearButton.boundingBox(),
  ]);
  expect(inputBox).not.toBeNull();
  expect(runBox).not.toBeNull();
  expect(clearBox).not.toBeNull();
  expect(inputBox!.height).toBeGreaterThanOrEqual(43);
  expect(runBox!.height).toBeGreaterThanOrEqual(43);
  expect(clearBox!.height).toBeGreaterThanOrEqual(43);

  const viewportWidth = page.viewportSize()?.width ?? 0;
  if (viewportWidth >= 640) {
    expect(runBox!.x).toBeGreaterThan(inputBox!.x + inputBox!.width);
    expect(clearBox!.x).toBeGreaterThan(runBox!.x + runBox!.width);
    expect(Math.abs(runBox!.y - inputBox!.y)).toBeLessThanOrEqual(1);
    expect(Math.abs(clearBox!.y - inputBox!.y)).toBeLessThanOrEqual(1);
    return;
  }

  expect(runBox!.y).toBeGreaterThan(inputBox!.y + inputBox!.height);
  expect(clearBox!.y).toBeGreaterThan(runBox!.y + runBox!.height);
  expect(Math.abs(runBox!.x - inputBox!.x)).toBeLessThanOrEqual(1);
  expect(Math.abs(clearBox!.x - inputBox!.x)).toBeLessThanOrEqual(1);
  expect(runBox!.width).toBeGreaterThanOrEqual(inputBox!.width - 1);
  expect(clearBox!.width).toBeGreaterThanOrEqual(inputBox!.width - 1);
}

async function expectGraphSearchFieldLayout(page: Page, playground: Locator) {
  const field = playground.getByTestId("ontology-graph-search-field");
  const input = playground.getByTestId("ontology-graph-search");
  const modeControl = playground.getByTestId("ontology-graph-view-mode");
  const detailsToggleField = playground.getByTestId("ontology-graph-details-toggle-field");

  await expect(field).toBeVisible();
  await expect(input).toBeVisible();

  const [fieldBox, inputBox, modeBox, detailsBox] = await Promise.all([
    field.boundingBox(),
    input.boundingBox(),
    modeControl.boundingBox(),
    detailsToggleField.boundingBox(),
  ]);
  expect(fieldBox).not.toBeNull();
  expect(inputBox).not.toBeNull();
  expect(modeBox).not.toBeNull();
  expect(detailsBox).not.toBeNull();

  const viewportWidth = page.viewportSize()?.width ?? 0;
  expect(fieldBox!.height).toBeGreaterThanOrEqual(viewportWidth < 640 ? 43 : 39);
  expect(fieldBox!.height).toBeLessThanOrEqual(45);
  expect(Math.abs(fieldBox!.height - modeBox!.height)).toBeLessThanOrEqual(1);
  expect(Math.abs(fieldBox!.height - detailsBox!.height)).toBeLessThanOrEqual(1);

  const fieldCenterY = fieldBox!.y + fieldBox!.height / 2;
  const inputCenterY = inputBox!.y + inputBox!.height / 2;
  expect(Math.abs(fieldCenterY - inputCenterY)).toBeLessThanOrEqual(1.5);
  expect(inputBox!.x).toBeGreaterThan(fieldBox!.x + 28);
  expect(inputBox!.x + inputBox!.width).toBeLessThanOrEqual(fieldBox!.x + fieldBox!.width - 10);
  expect(inputBox!.height).toBeLessThan(fieldBox!.height - 8);

  const inputFrameStyles = await input.evaluate((element) => {
    const style = window.getComputedStyle(element);
    return {
      borderTopWidth: style.borderTopWidth,
      borderRightWidth: style.borderRightWidth,
      borderBottomWidth: style.borderBottomWidth,
      borderLeftWidth: style.borderLeftWidth,
      boxShadow: style.boxShadow,
      outlineStyle: style.outlineStyle,
      paddingTop: style.paddingTop,
      paddingBottom: style.paddingBottom,
    };
  });
  expect(inputFrameStyles).toMatchObject({
    borderTopWidth: "0px",
    borderRightWidth: "0px",
    borderBottomWidth: "0px",
    borderLeftWidth: "0px",
    outlineStyle: "none",
    paddingTop: "0px",
    paddingBottom: "0px",
  });
  expect(hasVisibleBoxShadow(inputFrameStyles.boxShadow)).toBe(false);

  await playground.getByTestId("ontology-graph-mode-physical_er").focus();
  await page.keyboard.press("Tab");
  await expect
    .poll(() => page.evaluate(() => document.activeElement?.getAttribute("data-testid")))
    .toBe("ontology-graph-search");

  const focusedInputStyles = await input.evaluate((element) => {
    const style = window.getComputedStyle(element);
    return {
      borderTopWidth: style.borderTopWidth,
      boxShadow: style.boxShadow,
      outlineStyle: style.outlineStyle,
    };
  });
  expect(focusedInputStyles).toMatchObject({
    borderTopWidth: "0px",
    outlineStyle: "none",
  });
  expect(hasVisibleBoxShadow(focusedInputStyles.boxShadow)).toBe(false);

  const focusedFieldShadow = await field.evaluate(
    (element) => window.getComputedStyle(element).boxShadow
  );
  expect(hasVisibleBoxShadow(focusedFieldShadow)).toBe(true);

  if (viewportWidth >= 640) {
    const laneBox = await playground.getByTestId("ontology-graph-lane-business").boundingBox();
    expect(laneBox).not.toBeNull();
    expect(fieldBox!.y + fieldBox!.height + 4).toBeLessThanOrEqual(laneBox!.y);
  }
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
  if (testInfo.project.name === "desktop") {
    await page.setViewportSize({ width: 1440, height: 900 });
  }
  await mockApi(page);
  await page.goto("/ontology-build?profile=default");

  const playground = page.getByRole("region", { name: "質問の Ontology 接地確認" });
  await playground.scrollIntoViewIfNeeded();
  await expect(
    playground.getByText("質問を入力すると、一致したノードと関係をグラフで強調表示します。")
  ).toBeVisible();
  await expect(playground.getByTestId("ontology-playground-graph-summary")).toContainText(
    "確認対象: 5 ノード / 30 関係"
  );
  await expect(playground.getByTestId("ontology-playground-revision-id")).toContainText("rev1");
  await expect(playground.getByTestId("ontology-playground-clear")).toBeDisabled();
  await expectQuestionActionLayoutWithClear(page, playground);
  await expectNoHorizontalScroll(page);
  await openGraphIfCollapsed(page, playground);
  await expect(playground.getByTestId("ontology-graph-view-mode")).toBeVisible();
  await expect(playground.getByTestId("ontology-graph-mode-all")).toHaveAttribute("aria-pressed", "true");
  await expect(playground.getByTestId("ontology-graph-lane-business")).toContainText("業務概念");
  await expect(playground.getByTestId("ontology-graph-lane-attribute")).toContainText("属性・指標");
  await expect(playground.getByTestId("ontology-graph-lane-detail")).toContainText("物理列・列挙値");
  await expectGraphSearchFieldLayout(page, playground);
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
  await openGraphIfCollapsed(page, playground);

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

test("質問接地グラフで選択した物理オブジェクトの ER 詳細を段階表示する", async ({ page }) => {
  await mockApi(page, { ontologyGraph: erDetailOntologyGraph });
  await page.goto("/ontology-build?profile=default");

  const playground = page.getByRole("region", { name: "質問の Ontology 接地確認" });
  await playground.scrollIntoViewIfNeeded();
  await openGraphIfCollapsed(page, playground);
  await expect(playground.getByTestId("ontology-er-details-panel")).toHaveCount(0);

  await expect(playground.getByTestId("ontology-inspector-node-picker")).toBeVisible();
  await playground.getByTestId("ontology-inspector-node-employee-business").click();

  const details = playground.getByTestId("ontology-er-details-panel");
  await expect(details).toBeVisible();
  await expect(details.getByRole("heading", { name: "ER 詳細" })).toBeVisible();
  await expect(details.getByTestId("ontology-er-detail-object-name")).toContainText("ADMIN.EMPLOYEE");
  await expect(details).toContainText("列 4");

  const columns = details.getByTestId("ontology-er-columns");
  await expect(columns).toContainText("EMPLOYEE_ID");
  await expect(columns).toContainText("DEPARTMENT_ID");
  await expect(columns).toContainText("EMPLOYEE_NAME");
  await expect(columns).toContainText("HIRE_DATE");
  await expect(columns).toContainText("NUMBER");
  await expect(columns).toContainText("VARCHAR2");
  await expect(columns).toContainText("DATE");
  await expect(columns).toContainText("PK");
  await expect(columns).toContainText("FK");

  const joins = details.getByTestId("ontology-er-joins");
  await expect(joins).toContainText("所属部署を参照");
  await expect(joins).toContainText("ADMIN.EMPLOYEE.DEPARTMENT_ID = ADMIN.DEPARTMENT.DEPARTMENT_ID");
  await expect(playground.getByTestId("ontology-node-card-employee-table")).toContainText("列 4");
  await expect(playground.getByTestId("ontology-node-card-employee-table")).toContainText("Join 1");

  await playground.getByTestId("ontology-graph-details-toggle").check();
  await expect(playground.getByTestId("ontology-node-card-employee-department-id")).toBeVisible();
  await expect(details).toBeVisible();
  await expectNoHorizontalScroll(page);
});

test("質問を接地すると分類とグラフ強調が表示され、入力削除とクリアで reset できる", async ({
  page,
}) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await mockApi(page);
  await page.goto("/ontology-build?profile=default");

  const playground = page.getByRole("region", { name: "質問の Ontology 接地確認" });
  await playground.scrollIntoViewIfNeeded();
  await openGraphIfCollapsed(page, playground);
  await expect(playground.getByTestId("ontology-playground-clear")).toBeDisabled();
  await playground.getByTestId("ontology-playground-question").fill("顧客と注文の関係は?");
  await expect(playground.getByTestId("ontology-playground-clear")).toBeVisible();
  await expect(playground.getByTestId("ontology-playground-clear")).toBeEnabled();
  await expectQuestionActionLayoutWithClear(page, playground);
  await expectNoHorizontalScroll(page);
  await playground.getByTestId("ontology-playground-run").click();

  const result = playground.getByTestId("ontology-playground-result");
  await expect(result).toContainText("関係の一致");
  await expect(result).toContainText("注文する");
  await expect(playground.getByTestId("ontology-playground-ready-state")).toHaveCount(0);
  await expect(playground.getByTestId("ontology-graph-mode-grounding")).toHaveAttribute("aria-pressed", "true");
  await expect(playground.getByTestId("ontology-grounding-path-panel")).toContainText("接地パス");
  await expect(playground.getByTestId("ontology-grounding-path-panel")).toContainText("顧客");
  await expect(playground.getByTestId("ontology-grounding-path-panel")).toContainText("注文");
  await expect(playground.getByTestId("ontology-inspector-relationships")).toContainText("注文する");

  const metricCard = playground
    .locator(".react-flow__node", { hasText: "売上合計" })
    .locator("div")
    .first();
  await expect(metricCard).toHaveCSS("opacity", "0.35");
  await expect(playground.getByTestId("ontology-playground-clear")).toBeVisible();
  await expectQuestionActionLayoutWithClear(page, playground);
  await expectNoHorizontalScroll(page);

  await playground.getByTestId("ontology-playground-question").fill("");
  await expect(playground.getByTestId("ontology-playground-question")).toHaveValue("");
  await expect(playground.getByTestId("ontology-playground-ready-state")).toBeVisible();
  await expect(playground.getByTestId("ontology-playground-result")).toHaveCount(0);
  await expect(playground.getByTestId("ontology-graph-mode-all")).toHaveAttribute("aria-pressed", "true");
  await expect(metricCard).toHaveCSS("opacity", "1");
  await expect(playground.getByTestId("ontology-node-details-panel")).toContainText(
    "グラフ上のノードを選択"
  );
  await expect(playground.getByTestId("ontology-playground-clear")).toBeVisible();
  await expect(playground.getByTestId("ontology-playground-clear")).toBeDisabled();

  await playground.getByTestId("ontology-playground-question").fill("顧客と注文の関係は?");
  await expect(playground.getByTestId("ontology-playground-clear")).toBeVisible();
  await expect(playground.getByTestId("ontology-playground-clear")).toBeEnabled();
  await expectQuestionActionLayoutWithClear(page, playground);
  await playground.getByTestId("ontology-playground-run").click();
  await expect(playground.getByTestId("ontology-playground-result")).toContainText("関係の一致");
  await expect(metricCard).toHaveCSS("opacity", "0.35");
  await playground.getByTestId("ontology-playground-clear").click();
  await expect(playground.getByTestId("ontology-playground-question")).toHaveValue("");
  await expect(playground.getByTestId("ontology-playground-ready-state")).toBeVisible();
  await expect(playground.getByTestId("ontology-playground-result")).toHaveCount(0);
  await expect(playground.getByTestId("ontology-graph-mode-all")).toHaveAttribute("aria-pressed", "true");
  await expect(metricCard).toHaveCSS("opacity", "1");
  await expect(playground.getByTestId("ontology-node-details-panel")).toContainText(
    "グラフ上のノードを選択"
  );
  await expect(playground.getByTestId("ontology-playground-clear")).toBeVisible();
  await expect(playground.getByTestId("ontology-playground-clear")).toBeDisabled();
  await expectNoHorizontalScroll(page);
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
  await expect(playground.getByTestId("ontology-playground-inspector")).toBeVisible();
  await openGraphIfCollapsed(page, playground);
  await expect(playground.locator(".react-flow")).toBeVisible();
  await expect(playground.locator(".react-flow__node")).toHaveCount(3);
  await expect(playground.getByTestId("ontology-graph-lane-business")).toContainText("業務概念");
  await expect(playground.getByTestId("ontology-graph-mode-all")).toHaveAttribute("aria-pressed", "true");
  await expect(playground.getByTestId("ontology-graph-legend")).toBeVisible();
  await expect(playground.locator(".react-flow__node", { hasText: "顧客" })).toBeVisible();
  await expect(playground.locator(".react-flow__node", { hasText: "注文" })).toBeVisible();
  await expectNoHorizontalScroll(page);
});
