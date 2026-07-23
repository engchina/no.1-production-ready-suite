import { expect, test, type Page, type Route } from "@playwright/test";
import { mockDatabaseGateReady } from "./_helpers/database-gate";

test.beforeEach(async ({ page }) => mockDatabaseGateReady(page));

async function fulfillJson(route: Route, data: unknown) {
  await route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ data }),
  });
}

const profiles = [
  {
    id: "default",
    name: "既定プロファイル",
    category: "既定",
    description: "オントロジー連携の確認",
    allowed_tables: ["ORDERS", "CUSTOMERS"],
    allowed_views: [],
    glossary: {},
    sql_rules: [],
    default_row_limit: 100,
    safety_policy: "select_only",
    few_shot_examples: [],
    select_ai_config: null,
    archived: false,
  },
];

const ontologyView = {
  materialized: true,
  stale: false,
  profile_ontology_view: {
    id: "profile-view:default",
    profile_id: "default",
    ontology_revision_id: "revision-1",
    etag: "view-etag",
    node_ids: ["table:APP:ORDERS", "table:APP:CUSTOMERS"],
    edge_ids: ["fk:orders-customers"],
    allowed_path_ids: [],
  },
  ontology_graph: {
    revision: {
      id: "revision-1",
      version: 3,
      status: "published",
      schema_fingerprint: "fp",
      etag: "rev-etag",
    },
    nodes: [
      {
        id: "table:APP:ORDERS",
        kind: "table",
        business_name_ja: "受注",
        review_status: "approved",
        physical_mappings: [
          { object_ref: { owner: "APP", object_name: "ORDERS", object_type: "table" } },
        ],
      },
      {
        id: "table:APP:CUSTOMERS",
        kind: "table",
        business_name_ja: "顧客",
        review_status: "approved",
        physical_mappings: [
          { object_ref: { owner: "APP", object_name: "CUSTOMERS", object_type: "table" } },
        ],
      },
    ],
    edges: [
      {
        id: "fk:orders-customers",
        kind: "foreign_key",
        source_node_id: "table:APP:ORDERS",
        target_node_id: "table:APP:CUSTOMERS",
        relationship_name_ja: "顧客を参照",
        cardinality: "many_to_one",
        review_status: "approved",
      },
    ],
  },
};

const templates = [
  {
    id: "retail",
    metadata: {
      name_ja: "小売",
      description_ja: "顧客・商品・注文を中心とした小売/EC 業務の出発点テンプレート。",
      icon: "🛍️",
      category: "業種",
      tags: ["小売"],
    },
    entity_count: 4,
    relationship_count: 3,
    term_count: 2,
  },
  {
    id: "hr",
    metadata: {
      name_ja: "人事",
      description_ja: "社員・部門・勤怠を中心とした人事業務の出発点テンプレート。",
      icon: "👥",
      category: "業種",
      tags: ["人事"],
    },
    entity_count: 4,
    relationship_count: 3,
    term_count: 2,
  },
];

interface MockState {
  applyRequests: Array<Record<string, unknown>>;
  importRequests: number;
  proposalsFetches: number;
}

async function mockApi(page: Page): Promise<MockState> {
  const state: MockState = { applyRequests: [], importRequests: 0, proposalsFetches: 0 };
  await page.route("**/api/schema/catalog", (route) =>
    fulfillJson(route, { refreshed_at: "2026-07-12T00:00:00Z", tables: [] })
  );
  await page.route("**/api/nl2sql/profiles", (route) => fulfillJson(route, profiles));
  await page.route("**/api/nl2sql/profiles/search?*", (route) =>
    fulfillJson(route, {
      items: profiles.map((profile) => ({
        id: profile.id,
        name: profile.name,
        category: profile.category,
        description: profile.description,
        archived: profile.archived,
        allowed_table_count: profile.allowed_tables.length,
        allowed_view_count: profile.allowed_views.length,
        glossary_count: 0,
        few_shot_count: 0,
        version: 1,
        etag: "etag-default",
        updated_at: "2026-07-12T00:00:00Z",
      })),
      next_cursor: null,
      total: profiles.length,
      change_token: 1,
    })
  );
  await page.route(/\/api\/nl2sql\/profiles\/[^/?]+$/, (route) =>
    fulfillJson(route, profiles[0])
  );
  await page.route("**/api/nl2sql/profiles/*/ontology-view", (route) =>
    fulfillJson(route, ontologyView)
  );
  await page.route("**/api/nl2sql/profiles/*/ontology-view/mermaid", (route) =>
    fulfillJson(route, { mermaid: "erDiagram" })
  );
  await page.route("**/api/nl2sql/ontology/revisions", (route) =>
    fulfillJson(route, {
      revisions: [ontologyView.ontology_graph.revision],
      active_revision_id: "revision-1",
    })
  );
  await page.route("**/api/nl2sql/profiles/*/ontology-proposals", (route) => {
    state.proposalsFetches += 1;
    return fulfillJson(route, { proposals: [] });
  });
  await page.route("**/api/nl2sql/ontology-templates", (route) =>
    fulfillJson(route, { templates })
  );
  await page.route("**/api/nl2sql/profiles/*/ontology-templates/*/apply", (route) => {
    const payload = route.request().postDataJSON() as Record<string, unknown>;
    state.applyRequests.push(payload);
    const dryRun = payload.dry_run === true;
    return fulfillJson(route, {
      proposal_ids: dryRun ? [] : ["proposal-1", "proposal-2"],
      warnings_ja: [
        "テンプレートのエンティティ「商品」(PRODUCTS)を profile 範囲内に解決できないため、業務用語として提案します。",
      ],
      resolved: [
        { key: "customer", object_name: "APP.CUSTOMERS" },
        { key: "order", object_name: "APP.ORDERS" },
      ],
      unresolved: ["product"],
      proposal_count: 2,
      term_proposal_count: 3,
    });
  });
  await page.route("**/api/nl2sql/ontology/revisions/*/export**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/rdf+xml",
      headers: { "Content-Disposition": 'attachment; filename="ontology-revision-1.rdf"' },
      body: '<?xml version="1.0"?><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"/>',
    })
  );
  await page.route("**/api/nl2sql/profiles/*/ontology-import", (route) => {
    state.importRequests += 1;
    return fulfillJson(route, {
      proposal_ids: ["proposal-3"],
      warnings_ja: [],
      resolved: [{ key: "顧客", object_name: "APP.CUSTOMERS" }],
      unresolved: [],
      proposal_count: 1,
      term_proposal_count: 0,
      counts: { classes: 1, object_properties: 0, datatype_properties: 0, term_proposals: 0 },
    });
  });
  return state;
}

test("テンプレートのプレビューと適用が提案登録まで通る", async ({ page }) => {
  const state = await mockApi(page);
  await page.goto("/ontology-build?profile=default");

  const interchange = page.getByRole("region", { name: "オントロジー連携" });
  await expect(interchange).toBeVisible();
  await expect(page.getByTestId("ontology-template-retail")).toBeVisible();
  await expect(page.getByTestId("ontology-template-retail")).toContainText(
    "エンティティ 4 件"
  );

  await page.getByTestId("ontology-template-retail").click();
  await page.getByTestId("ontology-template-preview").click();
  const result = page.getByTestId("ontology-interchange-result");
  await expect(result).toContainText("登録される提案は 2 件です");
  await expect(result).toContainText("customer → APP.CUSTOMERS");
  await expect(result).toContainText("未解決: product");
  expect(state.applyRequests[0]?.dry_run).toBe(true);

  // 未解決エンティティへ override を入力して適用
  await page.getByPlaceholder("OWNER.OBJECT").fill("APP.ORDERS");
  const proposalsBefore = state.proposalsFetches;
  await page.getByTestId("ontology-template-apply").click();
  await expect(result).toContainText("提案を 2 件登録しました");
  expect(state.applyRequests[1]?.dry_run).toBe(false);
  expect(
    (state.applyRequests[1]?.overrides as Record<string, string>).product
  ).toBe("APP.ORDERS");
  // 適用後に提案一覧が再読込される
  await expect
    .poll(() => state.proposalsFetches)
    .toBeGreaterThan(proposalsBefore);
});

test("RDF エクスポートとインポートが動作する", async ({ page }) => {
  const state = await mockApi(page);
  await page.goto("/ontology-build?profile=default");

  const interchange = page.getByRole("region", { name: "オントロジー連携" });
  await expect(interchange).toBeVisible();

  const downloadPromise = page.waitForEvent("download");
  await page.getByTestId("ontology-export-download").click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe("ontology-revision-1.rdf");

  await page
    .getByTestId("ontology-import-file")
    .locator("input[type=file]")
    .setInputFiles({
      name: "external.rdf",
      mimeType: "application/rdf+xml",
      buffer: Buffer.from('<?xml version="1.0"?><rdf:RDF/>'),
    });
  await page.getByTestId("ontology-import-run").click();
  await expect(page.getByTestId("ontology-interchange-result")).toContainText(
    "提案を 1 件登録しました"
  );
  expect(state.importRequests).toBe(1);
});

test("決定論プレイグラウンドで関係辿りがハイライトされる", async ({ page }) => {
  await mockApi(page);
  await page.goto("/ontology-build?profile=default");

  const playground = page.getByRole("region", { name: "質問プレイグラウンド(決定論)" });
  await expect(playground).toBeVisible();
  await playground.scrollIntoViewIfNeeded();

  await page.getByTestId("ontology-playground-question").fill("受注と顧客の関係は?");
  await page.getByTestId("ontology-playground-run").click();

  const result = page.getByTestId("ontology-playground-result");
  await expect(result).toContainText("関係辿り");
  await expect(result).toContainText("顧客を参照");
  // 一致ノードは aria-label にも状態を持つ(color-not-only)
  await expect(
    playground.locator('[aria-label*="質問に一致"]').first()
  ).toBeVisible();
});

test("一致しない質問は候補を提示する", async ({ page }) => {
  await mockApi(page);
  await page.goto("/ontology-build?profile=default");

  const playground = page.getByRole("region", { name: "質問プレイグラウンド(決定論)" });
  await expect(playground).toBeVisible();
  await page.getByTestId("ontology-playground-question").fill("天気はどうですか?");
  await page.getByTestId("ontology-playground-run").click();
  await expect(page.getByTestId("ontology-playground-result")).toContainText("一致なし");
});
