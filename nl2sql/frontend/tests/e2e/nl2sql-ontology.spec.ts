import { expect, test, type Page, type Route } from "@playwright/test";
import { mockDatabaseGateReady } from "./_helpers/database-gate";

test.beforeEach(async ({ page }) => mockDatabaseGateReady(page));

const catalog = {
  refreshed_at: "2026-07-11T00:00:00Z",
  schema_fingerprint: "schema-fingerprint",
  tables: [
    {
      table_name: "ORDERS",
      logical_name: "受注",
      owner: "APP",
      table_type: "table",
      comment: "受注データ",
      row_count: 3,
      constraints: ["PK_ORDERS P(ID)"],
      constraint_details: [
        {
          constraint_name: "PK_ORDERS",
          constraint_type: "P",
          owner: "APP",
          table_name: "ORDERS",
          columns: ["ID"],
          referenced_columns: [],
        },
      ],
      columns: [
        {
          column_name: "ID",
          logical_name: "受注 ID",
          data_type: "NUMBER",
          nullable: false,
          comment: "受注 ID",
          sample_values: ["1"],
        },
      ],
    },
  ],
  view_dependencies: [],
};

const profile = {
  id: "default",
  name: "標準プロファイル",
  category: "販売",
  description: "受注分析",
  allowed_tables: ["ORDERS"],
  allowed_views: [],
  glossary: {},
  sql_rules: [],
  default_row_limit: 100,
  safety_policy: "select_only",
  few_shot_examples: [],
  select_ai_config: {
    profile_name: "",
    region: "",
    model: "",
    embedding_model: "cohere.embed-v4.0",
    max_tokens: 32000,
    enforce_object_list: true,
    comments: true,
    annotations: false,
    constraints: true,
    role: "",
    additional_instructions: "",
  },
  archived: false,
};

const graphNode = {
  id: "physical-orders",
  revision_id: "revision-1",
  kind: "table",
  technical_name: "APP.ORDERS",
  business_name_ja: "受注",
  description_ja: "受注データ",
  aliases: ["ORDERS"],
  physical_mappings: [],
  provenance: { source_kind: "introspected" },
  confidence: 1,
  review_status: "approved",
  metadata: { owner: "APP", object_name: "ORDERS" },
};

const metricNode = {
  ...graphNode,
  id: "metric-order-amount",
  kind: "metric",
  technical_name: "order_amount",
  business_name_ja: "受注金額",
  description_ja: "確定した受注の金額",
  aliases: ["売上金額"],
  metadata: {},
};

const orderDateNode = {
  ...graphNode,
  id: "property-order-date",
  kind: "property",
  technical_name: "APP.ORDERS.ORDER_DATE",
  business_name_ja: "受注日",
  description_ja: "受注が確定した日付",
  aliases: ["注文日"],
  metadata: { owner: "APP", object_name: "ORDERS", column_name: "ORDER_DATE" },
};

const orderStatusNode = {
  ...graphNode,
  id: "property-order-status",
  kind: "property",
  technical_name: "APP.ORDERS.STATUS",
  business_name_ja: "受注状態",
  description_ja: "受注の業務状態",
  aliases: ["ステータス"],
  metadata: { owner: "APP", object_name: "ORDERS", column_name: "STATUS" },
};

const statusRuleNode = {
  ...graphNode,
  id: "business-rule-order-status",
  kind: "business_rule",
  technical_name: "order_status_required",
  business_name_ja: "受注状態の必須ルール",
  description_ja: "受注状態は必須です。",
  aliases: [],
  metadata: {},
  business_rule_definition: {
    rule_kind: "validation",
    statement_ja: "受注状態は必須です。",
    applies_to_node_ids: ["property-order-status"],
    expression: { operator: "not_null", property_node_id: "property-order-status" },
    severity: "violation",
    execution_mode: "shacl",
  },
};

const confirmedStatusNode = {
  ...graphNode,
  id: "enum-order-status-confirmed",
  kind: "enum_value",
  technical_name: "order_status_confirmed",
  business_name_ja: "確定済み",
  description_ja: "確定した受注状態",
  aliases: ["確定"],
  metadata: {},
  enum_value_definition: {
    code: "CONFIRMED",
    label_ja: "確定済み",
    aliases: ["確定"],
    physical_literal: "CONFIRMED",
    data_type: "string",
    property_node_id: "property-order-status",
  },
};

const customerNode = {
  ...graphNode,
  id: "physical-customers",
  kind: "table",
  technical_name: "APP.CUSTOMERS",
  business_name_ja: "顧客",
  description_ja: "顧客データ",
  aliases: ["CUSTOMERS"],
  metadata: { owner: "APP", object_name: "CUSTOMERS" },
};

const orderCustomerEdge = {
  id: "edge-order-customer",
  revision_id: "revision-1",
  kind: "business_relationship",
  source_node_id: "physical-orders",
  target_node_id: "physical-customers",
  relationship_name_ja: "受注の顧客",
  description_ja: "受注を行った顧客",
  direction: "directed",
  cardinality: "many_to_one",
  join_conditions: [],
  allowed_join_types: ["inner", "left"],
  provenance: { source_kind: "curated" },
  confidence: 1,
  review_status: "approved",
  metadata: {},
};

const revision = {
  id: "revision-1",
  version: 1,
  status: "draft",
  schema_fingerprint: "schema-fingerprint",
  etag: "revision-etag",
  created_at: "2026-07-11T00:00:00Z",
};

const intent = {
  version: 1,
  question_original: "受注件数を表示",
  question_effective: "受注件数を表示",
  profile_view_id: "profile-view-1",
  ontology_revision_id: "revision-1",
  entities: [
    {
      id: "intent-entity-1",
      ontology_node_id: "physical-orders",
      name_ja: "受注",
      role: "subject",
      physical_object_ids: ["physical-orders"],
    },
  ],
  metrics: [
    {
      id: "intent-metric-1",
      ontology_node_id: "",
      name_ja: "受注件数",
      aggregation: "COUNT",
    },
  ],
  dimensions: [],
  filters: [],
  granularity: "",
  sorts: [],
  limit: 100,
  candidate_paths: [
    {
      id: "path-order-customer",
      name_ja: "受注から顧客",
      edge_ids: ["edge-order-customer"],
      node_ids: ["physical-orders", "physical-customers"],
      approved: true,
      explanation_ja: "Profile で確認済みの顧客関係",
    },
  ],
  selected_path_id: null,
  ambiguities: [],
  confidence: 0.9,
  created_at: "2026-07-11T00:00:00Z",
};

const validation = {
  id: "validation-1",
  intent_version: 1,
  sql_hash: "sql-hash",
  ontology_revision_id: "revision-1",
  is_valid: true,
  intent_coverage: 1,
  findings: [
    {
      id: "finding-1",
      code: "ONTOLOGY_THREE_WAY_VALIDATED",
      severity: "pass",
      message_ja: "質問、SQL、Profile の意味が一致しています。",
    },
  ],
  passed_count: 1,
  warning_count: 0,
  blocker_count: 0,
  validation_hash: "validation-hash",
  created_at: "2026-07-11T00:00:00Z",
};

const artifact = {
  id: "artifact-1",
  intent_version: 1,
  ontology_revision_id: "revision-1",
  sql: "SELECT COUNT(*) AS ORDER_COUNT FROM APP.ORDERS FETCH FIRST 100 ROWS ONLY",
  sql_hash: "sql-hash",
  generation_context_hash: "context-hash",
  semantic_graph: {
    version: 1,
    sql_hash: "sql-hash",
    dialect: "oracle",
    statement_type: "SELECT",
    raw_sql: "SELECT COUNT(*) AS ORDER_COUNT FROM APP.ORDERS FETCH FIRST 100 ROWS ONLY",
    parse_complete: true,
    ctes: [],
    tables: [
      {
        id: "sql-table-1",
        scope_id: "scope-1",
        owner: "APP",
        name: "ORDERS",
        qualified_name: "APP.ORDERS",
        source_sql: "APP.ORDERS",
        is_cte: false,
      },
    ],
    columns: [],
    joins: [],
    projections: [
      {
        id: "projection-1",
        scope_id: "scope-1",
        output_name: "ORDER_COUNT",
        expression_sql: "COUNT(*) AS ORDER_COUNT",
        referenced_columns: [],
        contains_aggregate: true,
        contains_window: false,
      },
    ],
    filters: [],
    aggregates: [
      {
        id: "aggregate-1",
        scope_id: "scope-1",
        function_name: "COUNT",
        expression_sql: "COUNT(*)",
        referenced_columns: [],
      },
    ],
    groups: [],
    having: [],
    orders: [],
    limit: 100,
    windows: [],
    set_operations: [],
    subqueries: [],
    lineage: [],
    parse_warnings: [],
  },
  validation_report: validation,
  created_at: "2026-07-11T00:00:00Z",
};

function session(status: string, withSql = false, confirmed = false) {
  return {
    id: "session-1",
    profile_id: "default",
    profile_view_id: "profile-view-1",
    ontology_revision_id: "revision-1",
    status,
    original_question: "受注件数を表示",
    current_intent_version: 1,
    intents: [intent],
    sql_artifacts: withSql ? [artifact] : [],
    current_sql_artifact_id: withSql ? "artifact-1" : null,
    intent_confirmed_version: withSql ? 1 : null,
    sql_confirmation: confirmed
      ? {
          artifact_id: "artifact-1",
          ontology_revision_id: "revision-1",
          intent_version: 1,
          sql_hash: "sql-hash",
          validation_hash: "validation-hash",
          generation_context_hash: "context-hash",
          confirmed_at: "2026-07-11T00:01:00Z",
        }
      : null,
    execution: null,
    proposal_ids: [],
    created_at: "2026-07-11T00:00:00Z",
    updated_at: "2026-07-11T00:00:00Z",
    error_code: "",
    error_message_ja: "",
  };
}

function sessionData(status: string, withSql = false, confirmed = false, done = false) {
  return {
    session: session(status, withSql, confirmed),
    profile_ontology_view: {
      id: "profile-view-1",
      profile_id: "default",
      ontology_revision_id: "revision-1",
      etag: "view-etag",
      node_ids: [
        "physical-orders",
        "metric-order-amount",
        "property-order-date",
        "property-order-status",
        "physical-customers",
        "business-rule-order-status",
        "enum-order-status-confirmed",
      ],
      edge_ids: ["edge-order-customer"],
      physical_objects: [
        { node_id: "physical-orders", owner: "APP", object_name: "ORDERS", object_type: "table" },
      ],
      allowed_path_ids: ["edge-order-customer"],
    },
    ontology_graph: {
      revision,
      nodes: [
        graphNode,
        metricNode,
        orderDateNode,
        orderStatusNode,
        customerNode,
        statusRuleNode,
        confirmedStatusNode,
      ],
      edges: [orderCustomerEdge],
    },
    result: done
      ? { columns: ["ORDER_COUNT"], rows: [{ ORDER_COUNT: 3 }], total: 1 }
      : null,
    performance_check: withSql
      ? { available: false, warning: "PLAN_TABLE を利用できません。" }
      : null,
  };
}

async function fulfill(route: Route, data: unknown) {
  await route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ data }),
  });
}

async function mockApi(page: Page) {
  const payloads: Record<string, unknown> = {};
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    // 共通 helper の認証 mock を優先し、catch-all で CurrentUser を空 object にしない。
    if (path === "/api/auth/me") return route.fallback();
    if (path === "/api/ready/database") {
      return fulfill(route, { status: "ok", check: "ok", detail: null });
    }
    if (path === "/api/nl2sql/persistence") {
      return fulfill(route, {
        mode: "oracle",
        ready: true,
        durable: true,
        writable: true,
        snapshot_loaded: true,
        reason_code: null,
        checked_at: "2026-07-19T00:00:00Z",
      });
    }
    if (path === "/api/schema/catalog/head") {
      return fulfill(route, {
        catalog_version: 1,
        schema_fingerprint: catalog.schema_fingerprint,
        refreshed_at: catalog.refreshed_at,
        object_count: catalog.tables.length,
        column_count: catalog.tables.reduce((total, table) => total + table.columns.length, 0),
        change_token: 1,
        etag: catalog.schema_fingerprint,
      });
    }
    if (path === "/api/schema/objects") {
      return fulfill(route, {
        items: catalog.tables.map((table) => ({
          owner: table.owner,
          object_name: table.table_name,
          object_type: table.table_type,
          logical_name: table.logical_name,
          comment: table.comment,
          row_count: table.row_count,
          column_count: table.columns.length,
          last_ddl_at: "",
        })),
        next_cursor: null,
        total: catalog.tables.length,
        catalog_version: 1,
      });
    }
    if (path.startsWith("/api/schema/objects/")) {
      return fulfill(route, {
        table: catalog.tables[0],
        dependencies: [],
        catalog_version: 1,
        etag: catalog.schema_fingerprint,
      });
    }
    if (path === "/api/schema/catalog") return fulfill(route, catalog);
    if (path === "/api/nl2sql/profiles/search") {
      return fulfill(route, {
        items: [
          {
            id: profile.id,
            name: profile.name,
            category: profile.category,
            description: profile.description,
            archived: profile.archived,
            allowed_table_count: profile.allowed_tables.length,
            allowed_view_count: profile.allowed_views.length,
            glossary_count: Object.keys(profile.glossary).length,
            few_shot_count: profile.few_shot_examples.length,
            version: 1,
            etag: "profile-etag",
            updated_at: "2026-07-11T00:00:00Z",
          },
        ],
        next_cursor: null,
        total: 1,
        change_token: 1,
      });
    }
    if (path === "/api/nl2sql/profiles" && request.method() === "GET") {
      return fulfill(route, [profile]);
    }
    if (path === "/api/nl2sql/profiles/default" && request.method() === "GET") {
      return fulfill(route, profile);
    }
    if (path === "/api/nl2sql/history") return fulfill(route, { items: [] });
    if (path === "/api/nl2sql/recommend-profile") {
      return fulfill(route, {
        recommended_profile_id: "default",
        recommended_profile_name: "標準プロファイル",
        confidence: 1,
        reason: "受注",
        rewritten_question: "受注件数を表示",
        recommended_allowed_objects: { table_names: ["ORDERS"], columns: {} },
        candidates: [],
      });
    }
    if (path === "/api/nl2sql/similar-history") return fulfill(route, { items: [] });
    if (path === "/api/nl2sql/ontology/profile-recommendations") {
      return fulfill(route, {
        recommendation: {
          id: "recommendation-1",
          question_hash: "a".repeat(64),
          ontology_revision_id: "revision-1",
          candidates: [
            {
              profile_id: "default",
              profile_name: "標準プロファイル",
              ontology_revision_id: "revision-1",
              score: 1,
              matched_scenarios_ja: ["受注分析"],
              matched_terms: ["受注"],
              reasons_ja: ["用語「受注」が一致しました。"],
            },
          ],
          expires_at: "2026-07-19T12:15:00Z",
        },
      });
    }
    if (path.endsWith("/ontology/profile-recommendations/recommendation-1/confirm")) {
      return fulfill(route, {
        recommendation: {
          id: "recommendation-1",
          question_hash: "a".repeat(64),
          ontology_revision_id: "revision-1",
          candidates: [],
          selected_profile_id: "default",
          selected_revision_id: "revision-1",
          expires_at: "2026-07-19T12:15:00Z",
        },
        confirmation_token: "profile-confirmation-token",
      });
    }
    if (path === "/api/nl2sql/query-sessions" && request.method() === "POST") {
      payloads.create = request.postDataJSON();
      return fulfill(route, sessionData("awaiting_intent_confirmation"));
    }
    if (path.endsWith("/intent") && request.method() === "PATCH") {
      payloads.patch = request.postDataJSON();
      return fulfill(route, sessionData("awaiting_intent_confirmation"));
    }
    if (path.endsWith("/generate-sql")) {
      payloads.generate = request.postDataJSON();
      return fulfill(route, sessionData("awaiting_sql_confirmation", true));
    }
    if (path.endsWith("/confirm-sql")) {
      payloads.confirm = request.postDataJSON();
      return fulfill(route, sessionData("awaiting_sql_confirmation", true, true));
    }
    if (path.endsWith("/execute")) {
      payloads.execute = request.postDataJSON();
      return fulfill(route, sessionData("done", true, true, true));
    }
    if (path.endsWith("/ontology-view") && request.method() === "GET") {
      const data = sessionData("awaiting_intent_confirmation");
      return fulfill(route, {
        profile_ontology_view: data.profile_ontology_view,
        ontology_graph: data.ontology_graph,
      });
    }
    if (
      path === "/api/nl2sql/ontology/revisions/revision-1/drafts" &&
      request.method() === "POST"
    ) {
      payloads.semanticDraft = request.postDataJSON();
      const data = sessionData("awaiting_intent_confirmation").ontology_graph;
      return fulfill(route, {
        ...data,
        revision: { ...revision, id: "revision-2", version: 2, etag: "revision-2-etag" },
      });
    }
    if (path === "/api/nl2sql/db-admin/views") {
      return fulfill(route, { runtime: "deterministic", items: [], warnings: [] });
    }
    if (path === "/api/nl2sql/select-ai/db-profiles") {
      return fulfill(route, { runtime: "deterministic", profiles: [], warnings: [] });
    }
    return fulfill(route, {});
  });
  return payloads;
}

async function startConfirmedOntologySession(page: Page) {
  const start = page.getByRole("button", { name: "質問を解釈" });
  await start.click();
  const recommendation = page.getByTestId("nl2sql-ontology-profile-recommendation");
  await expect(recommendation).toBeVisible();
  await recommendation.getByRole("button", { name: "この Profile を確認" }).click();
  await start.click();
}

test("質問と SQL の Ontology を二段階確認して hash binding で実行する", async ({ page }, testInfo) => {
  const payloads = await mockApi(page);
  await page.goto("/query");

  await page.getByLabel("検索クエリ").fill("受注件数を表示");
  await startConfirmedOntologySession(page);
  await expect(page.getByRole("tab", { name: "質問の解釈" })).toBeVisible();
  await expect(page.getByText("受注件数", { exact: true }).filter({ visible: true }).first()).toBeVisible();

  await page.getByRole("button", { name: "確認して SQL を生成" }).click();
  await page.getByRole("tab", { name: "差分・確認" }).click();
  await expect(page.getByText("質問、SQL、Profile の意味が一致しています。")).toBeVisible();
  await page.getByRole("button", { name: "SQL の意味を確認" }).click();
  await page.getByRole("button", { name: "確認済み SQL を実行" }).click();

  await expect(page.getByRole("columnheader", { name: "ORDER_COUNT" })).toBeVisible();
  await expect(page.getByRole("cell", { name: "3" })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("ontology-query.png"), fullPage: true });
  expect(payloads.create).toMatchObject({
    question: "受注件数を表示",
    profile_id: "default",
    profile_confirmation_token: "profile-confirmation-token",
  });
  expect(payloads.generate).toMatchObject({ confirm_intent: true, intent_version: 1 });
  expect(payloads.confirm).toMatchObject({
    sql_hash: "sql-hash",
    validation_hash: "validation-hash",
    generation_context_hash: "context-hash",
    confirm_sql: true,
  });
  expect(payloads.execute).toMatchObject({ confirm_sql: true, session_id: "session-1" });
});

test("非 SQL 利用者が質問の解釈をフォームで修正し、明示操作後だけ patch を送信する", async ({ page }, testInfo) => {
  const payloads = await mockApi(page);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/query");

  await page.getByLabel("検索クエリ").fill("受注件数を表示");
  await startConfirmedOntologySession(page);

  const openEditor = page.getByRole("button", { name: "解釈を編集" });
  await openEditor.focus();
  await expect(openEditor).toBeFocused();
  await openEditor.press("Enter");
  await expect(page.getByLabel("確認に使う質問")).toBeVisible();

  await page.getByLabel("確認に使う質問").fill("確定した受注の月別売上金額");
  const metricGroup = page.getByRole("group", { name: "確認したい指標" });
  await metricGroup.getByLabel("指標", { exact: true }).selectOption({ label: "受注金額" });
  await metricGroup.getByLabel("まとめ方").selectOption({ label: "合計する" });

  const dimensionGroup = page.getByRole("group", { name: "結果の切り口" });
  await dimensionGroup.getByRole("button", { name: "切り口を追加" }).click();
  await dimensionGroup.getByLabel("切り口", { exact: true }).selectOption({ label: "受注日" });
  await dimensionGroup.getByLabel("切り口の粒度").selectOption({ label: "月ごと" });

  const filterGroup = page.getByRole("group", { name: "絞り込み条件" });
  await filterGroup.getByRole("button", { name: "条件を追加" }).click();
  await filterGroup.getByLabel("対象項目").selectOption({ label: "受注状態" });
  await filterGroup.getByLabel("条件の値").fill("確定");

  const timeGroup = page.getByRole("group", { name: "期間と全体の粒度" });
  await timeGroup.getByLabel("日付・時刻の項目").selectOption({ label: "受注日" });
  await timeGroup.getByLabel("開始日").fill("2026-06-01");
  await timeGroup.getByLabel("終了日").fill("2026-06-30");
  await timeGroup.getByLabel("相対的な期間").fill("先月");
  await timeGroup.getByLabel("結果全体の粒度").selectOption({ label: "月ごと" });

  const sortGroup = page.getByRole("group", { name: "並び順と件数" });
  await sortGroup.getByRole("button", { name: "並び順を追加" }).click();
  await sortGroup.getByLabel("並べる項目").selectOption({ label: "受注金額" });
  await sortGroup.getByLabel("方向").selectOption({ label: "大きい順・新しい順" });
  await sortGroup.getByLabel("最大件数").fill("50");
  await page.getByRole("group", { name: "利用する関係経路" })
    .getByLabel("候補経路")
    .selectOption({ label: "受注から顧客 (確認済み)" });

  expect(payloads.patch).toBeUndefined();
  await expect(page.getByText(/変更 \d+ 件/)).toBeVisible();
  await page.getByRole("button", { name: "変更を適用" }).click();
  await expect.poll(() => payloads.patch).toBeTruthy();

  const patch = payloads.patch as {
    base_version: number;
    operations: Array<{ path: string; value: unknown }>;
  };
  const operations = new Map(patch.operations.map((operation) => [operation.path, operation.value]));
  expect(patch.base_version).toBe(1);
  expect(operations.get("/question_effective")).toBe("確定した受注の月別売上金額");
  expect(operations.get("/metrics")).toEqual([
    expect.objectContaining({
      ontology_node_id: "metric-order-amount",
      name_ja: "受注金額",
      aggregation: "SUM",
    }),
  ]);
  expect(operations.get("/filters")).toEqual([
    expect.objectContaining({
      property_node_id: "property-order-status",
      label_ja: "受注状態",
      value: "確定",
    }),
  ]);
  expect(operations.get("/time_range")).toEqual(
    expect.objectContaining({
      property_node_id: "property-order-date",
      start: "2026-06-01",
      end: "2026-06-30",
      relative_expression: "先月",
    })
  );
  expect(operations.get("/granularity")).toBe("month");
  expect(operations.get("/limit")).toBe(50);
  expect(operations.get("/selected_path_id")).toBe("path-order-customer");

  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  expect(overflow).toBe(false);
  await page.screenshot({ path: testInfo.outputPath("ontology-intent-editor.png"), fullPage: true });
});

test("対象オブジェクトは業務プロファイル、オントロジー構築/物理・業務モデルは専用ページに分離される", async ({ page }, testInfo) => {
  await mockApi(page);

  // 業務プロファイル編集: 対象オブジェクト一覧は常時表示、オントロジー(構築/モデル)は非表示。
  await page.goto("/profiles");
  await page.getByRole("button", { name: "編集", exact: true }).first().click();
  await expect(page.getByTestId("profile-allowed-table-list")).toBeVisible();
  await expect(page.getByTestId("profile-ontology-build")).toHaveCount(0);
  await expect(page.locator('section[aria-label="物理・業務モデル"]')).toHaveCount(0);

  // 専用ページは URL 復元可能な業務モデル tab で表示する。
  await page.goto("/ontology-build?profile=default&tab=model");
  const modelSection = page.locator('section[aria-label="物理・業務モデル"]');
  await expect(modelSection).toBeVisible();
  await expect(page.getByText("受注", { exact: true }).filter({ visible: true }).first()).toBeVisible();
  await expect(page.getByTestId("profile-ontology-build")).toHaveCount(0);
  await modelSection.scrollIntoViewIfNeeded();
  await page.screenshot({ path: testInfo.outputPath("ontology-build.png"), fullPage: true });

  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  expect(overflow).toBe(false);
});

test("業務ルールと列挙値をキーボード操作できるフォームで確認し、revision Draft に保存する", async ({
  page,
}) => {
  const payloads = await mockApi(page);
  await page.goto("/ontology-build?profile=default&tab=model");

  const target = page.getByLabel("Inspector の編集対象");
  await target.focus();
  await target.selectOption("node:business-rule-order-status");
  await page
    .getByRole("textbox", { name: "業務ルール", exact: true })
    .fill("受注状態を必須にします。");
  await page.getByLabel("重大度").selectOption("warning");
  await page.getByRole("button", { name: "意味定義を Draft に保存" }).click();

  await expect(page.getByText("新しい Ontology revision の Draft に保存しました。")).toBeVisible();
  await expect.poll(() => payloads.semanticDraft).toBeTruthy();
  expect(payloads.semanticDraft).toMatchObject({
    base_etag: "revision-etag",
    node_upserts: [
      expect.objectContaining({
        id: "business-rule-order-status",
        review_status: "approved",
        business_rule_definition: expect.objectContaining({
          statement_ja: "受注状態を必須にします。",
          severity: "warning",
          execution_mode: "shacl",
        }),
      }),
    ],
  });

  await target.selectOption("node:enum-order-status-confirmed");
  await expect(page.getByLabel("列挙コード", { exact: true })).toHaveValue("CONFIRMED");
  await expect(page.getByLabel("日本語ラベル")).toHaveValue("確定済み");
  await expect(page.getByLabel("対象属性")).toHaveValue("property-order-status");
});
