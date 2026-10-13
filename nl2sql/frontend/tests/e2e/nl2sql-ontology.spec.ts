import { expect, test, type Page, type Route } from "./_helpers/test";
import { mockDatabaseGateReady } from "./_helpers/database-gate";
import { chooseSelectFieldOption, expectSelectFieldValue } from "./_helpers/select-field";
import {
  REQUIREMENT_CLARIFIED_QUESTION,
  REQUIREMENT_QUESTION,
  REQUIREMENT_SESSION_ID,
  requirementSessionData,
  type RequirementStage,
} from "./_helpers/requirement-session";

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
  limit: null as number | null,
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
  sql: "SELECT COUNT(*) AS ORDER_COUNT FROM APP.ORDERS",
  sql_hash: "sql-hash",
  generation_context_hash: "context-hash",
  semantic_graph: {
    version: 1,
    sql_hash: "sql-hash",
    dialect: "oracle",
    statement_type: "SELECT",
    raw_sql: "SELECT COUNT(*) AS ORDER_COUNT FROM APP.ORDERS",
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
    limit: null as number | null,
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

interface MockApiOptions {
  guidedStartDelayMs?: number;
  /** AI要件確認の作成の応答（#1439）。 */
  createStage?: RequirementStage;
  /** 回答の応答。 */
  answerStage?: RequirementStage;
  /** AI要件確認の作成を失敗させる（HTTP status と応答の本文。#1715）。 */
  createFailure?: { status: number; body: Record<string, unknown> };
}

async function waitForMockDelay(delayMs: number) {
  if (delayMs <= 0) return;
  await new Promise((resolve) => setTimeout(resolve, delayMs));
}

async function mockApi(
  page: Page,
  options: MockApiOptions = {},
) {
  const payloads: Record<string, unknown> = {};
  const guidedStartDelayMs = options.guidedStartDelayMs ?? 0;
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    // 共通 helper の認証 mock を優先し、catch-all で CurrentUser を空 object にしない。
    if (path === "/api/auth/me") return route.fallback();
    if (path === "/api/nl2sql/synthetic-data/runs") return fulfill(route, []);
    // profile-access 一覧(配列)は共通 helper の mock([])へ委譲する。
    if (path === "/api/security/profile-access/profiles") return route.fallback();
    if (path === "/api/nl2sql/rewrite") {
      // rewrite 既定 ON。catch-all の空 object を返すと比較カードが欠損 payload になるため echo する。
      const body = request.postDataJSON() as { question?: string };
      const original = String(body?.question ?? "");
      return fulfill(route, {
        original_question: original,
        rewritten_question: original,
        source: "deterministic",
        model: "",
        warnings: [],
      });
    }
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
        allowed_tables: profile.allowed_tables,
        allowed_views: profile.allowed_views,
        glossary: profile.glossary,
        sql_rules: profile.sql_rules,
        default_row_limit: profile.default_row_limit,
        safety_policy: profile.safety_policy,
        few_shot_examples: profile.few_shot_examples,
        select_ai_config: profile.select_ai_config,
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
    if (path === "/api/nl2sql/profiles/default/usage-context" && request.method() === "GET") {
      return fulfill(route, {
        id: profile.id,
        name: profile.name,
        category: profile.category,
        description: profile.description,
        allowed_tables: profile.allowed_tables,
        allowed_views: profile.allowed_views,
        archived: profile.archived,
        object_scope_version: 1,
        version: 1,
        etag: "profile-etag",
        updated_at: "2026-07-11T00:00:00Z",
      });
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
      await waitForMockDelay(guidedStartDelayMs);
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
      await waitForMockDelay(guidedStartDelayMs);
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
    if (path === "/api/nl2sql/synthetic-data/runs") return fulfill(route, []);
    if (path === "/api/nl2sql/jobs" && request.method() === "POST") {
      payloads.job = request.postDataJSON();
      return fulfill(route, {
        job_id: "job-ontology-1",
        status: "running",
        created_at: "2026-07-11T00:00:00Z",
        steps: [
          { stage: "prepare_context", status: "done", elapsed_ms: 8 },
          { stage: "generate_sql", status: "running", elapsed_ms: null },
          { stage: "safety_check", status: "pending", elapsed_ms: null },
          { stage: "execute_sql", status: "pending", elapsed_ms: null },
          { stage: "format_results", status: "pending", elapsed_ms: null },
        ],
      });
    }
    if (path === "/api/nl2sql/jobs/job-ontology-1" && request.method() === "GET") {
      const question = String(
        (payloads.job as { question?: unknown } | undefined)?.question ?? "受注件数を表示"
      );
      return fulfill(route, {
        job_id: "job-ontology-1",
        status: "done",
        created_at: "2026-07-11T00:00:00Z",
        started_at: "2026-07-11T00:00:00Z",
        finished_at: "2026-07-11T00:00:01Z",
        elapsed_ms: 1000,
        error_message: null,
        warning_message: null,
        steps: [
          { stage: "prepare_context", status: "done", elapsed_ms: 8 },
          { stage: "generate_sql", status: "done", elapsed_ms: 120 },
          { stage: "safety_check", status: "done", elapsed_ms: 10 },
          { stage: "execute_sql", status: "done", elapsed_ms: 20 },
          { stage: "format_results", status: "done", elapsed_ms: 5 },
        ],
        timing: {
          created_at: "2026-07-11T00:00:00Z",
          started_at: "2026-07-11T00:00:00Z",
          finished_at: "2026-07-11T00:00:01Z",
          elapsed_ms: 1000,
          stage_timings: [],
        },
        result: {
          history_id: "hist-ontology-1",
          engine: "select_ai",
          engine_meta: { profile: "NL2SQL_DEFAULT_PROFILE" },
          fallback_reason: "",
          original_question: question,
          rewritten_question: question,
          generated_sql: "SELECT COUNT(*) AS ORDER_COUNT FROM APP.ORDERS",
          executable_sql: "SELECT COUNT(*) AS ORDER_COUNT FROM APP.ORDERS",
          explanation: "受注件数を集計します。",
          safety: {
            is_safe: true,
            is_select_only: true,
            row_limit_applied: 0,
            blocked_reason: "",
            warnings: [],
            referenced_tables: ["APP.ORDERS"],
            referenced_columns: [],
          },
          recommendations: ["公開済みオントロジー context を利用しました。"],
          repaired_sql: "",
          optimization_hints: [],
          results: {
            columns: ["ORDER_COUNT"],
            rows: [{ ORDER_COUNT: 3 }],
            total: 1,
          },
          timing: {
            created_at: "2026-07-11T00:00:00Z",
            started_at: "2026-07-11T00:00:00Z",
            finished_at: "2026-07-11T00:00:01Z",
            elapsed_ms: 1000,
            stage_timings: [],
          },
          interpretation: {
            available: true,
            question: {
              available: true,
              source: "deterministic",
              original_question: question,
              rewritten_question: question,
              profile_id: "default",
              profile_name: "標準プロファイル",
              profile_category: "販売",
              target_objects: ["APP.ORDERS"],
              filters: [],
              group_by: [],
              order_by: [],
              aggregations: ["COUNT"],
              row_limit: null,
              confidence: 0.9,
              warnings: [],
            },
            sql: {
              available: true,
              source: "deterministic",
              summary: "受注件数を集計します。",
              statement_type: "SELECT",
              tables: ["APP.ORDERS"],
              columns: ["ORDER_COUNT"],
              joins: [],
              filters: [],
              aggregations: ["COUNT"],
              group_by: [],
              order_by: [],
              limit: null,
              semantic_graph: artifact.semantic_graph,
              warnings: [],
            },
            ontology_graph: sessionData("awaiting_intent_confirmation").ontology_graph,
            warnings: [],
          },
          show_prompt: null,
        },
      });
    }
    if (path === "/api/nl2sql/query-sessions" && request.method() === "POST") {
      payloads.create = request.postDataJSON();
      if ((payloads.create as { clarification_mode?: string }).clarification_mode === "guided") {
        await waitForMockDelay(guidedStartDelayMs);
        if (options.createFailure) {
          payloads.createFailures = Number(payloads.createFailures ?? 0) + 1;
          return route.fulfill({ status: options.createFailure.status, json: options.createFailure.body });
        }
        return fulfill(route, requirementSessionData(options.createStage ?? "needs_answer"));
      }
      return fulfill(route, sessionData("awaiting_intent_confirmation"));
    }
    if (path.endsWith("/clarification-answers") && request.method() === "POST") {
      payloads.clarificationAnswer = request.postDataJSON();
      return fulfill(route, requirementSessionData(options.answerStage ?? "ready", 2));
    }
    if (path.endsWith("/apply") && request.method() === "POST") {
      const body = request.postDataJSON() as { base_version: number };
      payloads.apply = body;
      return fulfill(route, requirementSessionData("applied", body.base_version));
    }
    if (path.endsWith("/cancel") && request.method() === "POST") {
      payloads.cancel = true;
      const data = requirementSessionData("needs_answer");
      Object.assign(data.session, { status: "cancelled" });
      return fulfill(route, data);
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
    if (path.endsWith("/ontology-markdown") && request.method() === "GET") {
      // draft が空だと editor でなく空状態 placeholder が出るため、編集入口検証用に本文を持たせる。
      return fulfill(route, {
        draft_markdown: "# オントロジー下書き\n\n- APP.DEPARTMENT: 部門\n",
        published_markdown: "",
        draft_revision: null,
        published_revision: null,
        draft_etag: "draft-etag-1",
        published_at: null,
      });
    }
    if (path.endsWith("/ontology-source-documents") && request.method() === "GET") {
      return fulfill(route, { source_documents: [] });
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

async function runCurrentOntologySearch(page: Page) {
  await page.getByRole("button", { name: "SQL を生成して実行" }).click();
  await expect(page.getByRole("columnheader", { name: "ORDER_COUNT" })).toBeVisible();
}

test("SQL を生成して実行すると公開済みオントロジー context を使って結果と解釈を表示する", async ({ page }, testInfo) => {
  const payloads = await mockApi(page);
  await page.goto("/query");

  await page.locator("#nl2sql-question-input").fill("受注件数を表示");
  await runCurrentOntologySearch(page);

  await expect(page.getByRole("columnheader", { name: "ORDER_COUNT" })).toBeVisible();
  await expect(page.getByRole("cell", { name: "3" })).toBeVisible();
  await expect(page.getByTestId("nl2sql-interpretation-panel")).toBeVisible();
  await expect(page.getByTestId("nl2sql-sql-grounding-panel")).toBeVisible();
  await expect(page.getByText("入力と生成 SQL の対応")).toHaveCount(0);
  await expect(page.getByText("入力テンプレート")).toHaveCount(0);
  await expect(page.getByText("生成 SQL の意味")).toHaveCount(0);
  await page.screenshot({ path: testInfo.outputPath("ontology-query.png"), fullPage: true });
  expect(payloads.job).toMatchObject({
    question: "受注件数を表示",
    profile_id: "default",
    use_ontology_context: true,
    include_interpretation: true,
  });
});

test("SQL の生成と実行は明示操作後だけ現在の質問とオントロジー利用設定を送信する", async ({ page }, testInfo) => {
  const payloads = await mockApi(page);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/query");

  await page.locator("#nl2sql-question-input").fill("受注件数を表示");
  expect(payloads.job).toBeUndefined();
  await page.locator("#nl2sql-question-input").fill("確定した受注の月別売上金額");
  await runCurrentOntologySearch(page);
  expect(payloads.job).toMatchObject({
    question: "確定した受注の月別売上金額",
    profile_id: "default",
    use_ontology_context: true,
    include_interpretation: true,
  });

  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  expect(overflow).toBe(false);
  await page.screenshot({ path: testInfo.outputPath("ontology-intent-editor.png"), fullPage: true });
});

async function startRequirementCheck(page: Page, question = REQUIREMENT_QUESTION) {
  await page.locator("#nl2sql-question-input").fill(question);
  await page.getByRole("button", { name: "AI要件確認", exact: true }).click();
  const panel = page.getByTestId("nl2sql-guided-clarification");
  await expect(panel).toBeVisible();
  return panel;
}

async function expectNoPageOverflow(page: Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  expect(overflow).toBe(false);
}

{
  // desktop（1280px）と mobile-375 の 2 つの project で動く。
  test("AI要件確認は SQL の要件を欄ごとに見せ、1 問ずつ確かめて反映する", async ({ page }, testInfo) => {
    const viewport = { name: testInfo.project.name };
    const payloads = await mockApi(page);
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.goto("/query");

    // 補足の説明は常設せず、ボタンの横の info アイコンを押すと出す（#901）。
    const clarificationDescription = page.getByTestId("nl2sql-clarification-description");
    await expect(clarificationDescription).toBeHidden();
    await page.getByRole("button", { name: "AI要件確認の説明", exact: true }).click();
    await expect(clarificationDescription).toHaveText(
      "質問と、データベースの Schema・業務プロファイル・用語集・オントロジーを合わせて、SQL を作るのに足りない情報・明確でない情報を 1 つずつ確認します。表のデータは読みません。"
    );
    await page.locator("#nl2sql-question-input").click();

    const panel = await startRequirementCheck(page);
    // 欄: SELECT / FROM と結合 / WHERE と期間 / GROUP BY / ORDER BY と件数 / WITH / そのほか。
    const requirements = panel.getByTestId("nl2sql-clarification-requirements");
    for (const group of [
      "表示する項目（SELECT）",
      "対象の表と結合（FROM・JOIN）",
      "条件と期間（WHERE）",
      "集計の単位（GROUP BY）",
      "並び順と件数の上限（ORDER BY・FETCH FIRST）",
      "WITH・サブクエリ",
      "そのほか",
    ]) {
      await expect(requirements.getByRole("heading", { name: group, exact: true })).toBeVisible();
    }
    // 表・列は物理名（論理名は括弧）で示す。AI の推測は一覧に出し、1 つずつは聞かない。
    const selectGroup = panel.getByTestId("nl2sql-requirement-group-select");
    await expect(selectGroup.getByText("CUSTOMERS.REGION（地域）、ORDERS.AMOUNT（受注金額）")).toBeVisible();
    await expect(selectGroup.getByText("AI の推測")).toBeVisible();
    await expect(panel.getByTestId("nl2sql-requirement-group-from")).toContainText("APP.ORDERS（受注）、APP.CUSTOMERS（顧客）");
    await expect(panel.getByTestId("nl2sql-requirement-group-subquery")).toContainText("なし");
    await expect(panel.getByText("AI の再検査 0/3 回")).toBeVisible();

    // 質問は 1 問ずつ（不足・あいまいだけ）。相対的な期間は具体的な日付の範囲で選ぶ。
    const question = panel.getByTestId("nl2sql-clarification-question");
    await expect(question).toHaveAttribute("data-item-key", "period_this_fiscal_year");
    await expect(question.getByText("確認 1・残り 1 件")).toBeVisible();
    const heading = question.getByRole("heading", { name: "「今年度」は、どの期間ですか？" });
    await expect(heading).toBeFocused();
    await expect(question).toContainText("基準日は実行日（2026-10-11）");
    await expect(question.getByRole("radio")).toHaveCount(3);
    await expect(panel.getByRole("button", { name: "要件を SQL の生成に反映" })).toHaveCount(0);
    const answerButton = question.getByRole("button", { name: "この内容で回答" });
    await expect(answerButton).toBeDisabled();
    await expect(answerButton.locator("..").getByRole("button")).toHaveText([
      "この内容で回答",
      "確認を中止して閉じる",
    ]);
    await page.screenshot({ path: testInfo.outputPath(`requirement-question-${viewport.name}.png`), fullPage: true });
    await panel.screenshot({ path: testInfo.outputPath(`requirement-panel-question-${viewport.name}.png`) });
    await expectNoPageOverflow(page);

    await question.getByRole("radio", { name: /2026-04-01〜2027-03-31/ }).check();
    await answerButton.click();

    // すべて答えたら、まとめ（書き直した質問・SQL の骨組み）を見せてから反映する。
    await expect(panel.locator('[data-status-variant="success"]').filter({ hasText: "確認完了" })).toBeVisible();
    await expect(panel.getByText("AI の再検査 1/3 回")).toBeVisible();
    const summary = panel.getByTestId("nl2sql-clarification-summary");
    await expect(summary.getByTestId("nl2sql-clarified-question")).toHaveText(REQUIREMENT_CLARIFIED_QUESTION);
    const skeleton = summary.getByTestId("nl2sql-clarification-skeleton");
    await expect(skeleton).toContainText("FROM APP.ORDERS");
    await expect(skeleton).toContainText("JOIN APP.CUSTOMERS ON ORDERS.CUSTOMER_ID = CUSTOMERS.CUSTOMER_ID");
    await expect(skeleton).toContainText("WHERE ORDERS.ORDER_DATE >= DATE '2026-04-01'");
    await expect(summary).toContainText("実行はせず、生成する SQL そのものでもありません");
    await expect(panel.getByTestId("nl2sql-clarification-question")).toHaveCount(0);
    await page.screenshot({ path: testInfo.outputPath(`requirement-summary-${viewport.name}.png`), fullPage: true });
    await panel.screenshot({ path: testInfo.outputPath(`requirement-panel-summary-${viewport.name}.png`) });
    await expectNoPageOverflow(page);

    await panel.getByRole("button", { name: "要件を SQL の生成に反映" }).click();
    const questionInput = page.locator("#nl2sql-question-input");
    await expect(panel).toHaveCount(0);
    await expect(questionInput).toHaveValue(REQUIREMENT_CLARIFIED_QUESTION);
    await expect(questionInput).toBeFocused();
    await expect(page.getByTestId("nl2sql-requirement-active")).toContainText("AI要件確認の要件を SQL の生成に渡します");
    expect(payloads.create).toMatchObject({
      question: REQUIREMENT_QUESTION,
      profile_id: "default",
      clarification_mode: "guided",
    });
    expect(payloads.clarificationAnswer).toEqual({
      base_version: 1,
      item_id: "item-period",
      selected_candidate_ids: ["candidate-fy-april"],
      free_text: "",
    });
    expect(payloads.apply).toEqual({ base_version: 2 });
    // 反映した session は閉じる（中止は送らない）。
    expect(payloads.cancel).toBeUndefined();

    // 構造化した要件は session の ID で SQL の生成に渡す。
    await runCurrentOntologySearch(page);
    expect(payloads.job).toMatchObject({
      question: REQUIREMENT_CLARIFIED_QUESTION,
      requirement_session_id: REQUIREMENT_SESSION_ID,
    });
    // 質問の文を書き換えたら、要件は渡さない。
    await questionInput.fill(`${REQUIREMENT_CLARIFIED_QUESTION} 追記`);
    await expect(page.getByTestId("nl2sql-requirement-dropped")).toBeVisible();
    await runCurrentOntologySearch(page);
    expect((payloads.job as Record<string, unknown>).requirement_session_id).toBeUndefined();
    await expectNoPageOverflow(page);
  });
}

test("AI要件確認の開始中は実処理に合わせて案内を切り替え経過時間を表示する", async ({ page }, testInfo) => {
  await mockApi(page, { guidedStartDelayMs: 1_500 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/query");

  const panel = await startRequirementCheck(page);
  const progress = panel.getByTestId("nl2sql-guided-start-progress");
  const timer = panel.getByTestId("nl2sql-guided-start-progress-timer");
  await expect(progress).toBeVisible();
  await expect(progress).toHaveAttribute("data-processing-placement", "panel");
  await expect(progress).toHaveAttribute("aria-busy", "true");
  const visibleProgressLabel = progress.locator("span.min-w-0.break-words");
  await expect(visibleProgressLabel).toHaveText("質問に合う業務プロファイルを確認しています");
  await expect(timer).toContainText("経過時間");
  await expect(timer).toHaveAttribute("role", "timer");
  await expect(timer).toHaveAttribute("aria-live", "off");
  // 共有 Spinner(svg.animate-spin)。reduced motion でも回転を止めない(#440)。
  const spinner = progress.locator("svg.animate-spin");
  await expect(spinner).toHaveCount(1);
  await expect(spinner).toHaveCSS("width", "16px");
  await expect(spinner).toHaveCSS("animation-name", "spin");

  await expect(timer).toContainText(/00:0[1-9]/);
  await expect(visibleProgressLabel).toHaveText("利用する業務プロファイルを確定しています");
  await expect(visibleProgressLabel).toHaveText("SQL の要件の下書きを作っています");
  await expect(timer).toContainText(/00:0[2-9]/);
  await page.screenshot({
    path: testInfo.outputPath("guided-clarification-progress.png"),
    fullPage: true,
  });

  await expect(panel.getByRole("heading", { name: "「今年度」は、どの期間ですか？" })).toBeVisible();
  await expect(progress).toHaveCount(0);
  await expectNoPageOverflow(page);
});

test("AI要件確認は開始処理の途中でも中止して閉じられる", async ({ page }, testInfo) => {
  const payloads = await mockApi(page, { guidedStartDelayMs: 1_500 });
  await page.goto("/query");

  const questionInput = page.locator("#nl2sql-question-input");
  const panel = await startRequirementCheck(page);
  await expect(panel.getByTestId("nl2sql-guided-start-progress")).toBeVisible();
  const closeButton = panel.getByRole("button", { name: "確認を中止して閉じる" });
  await expect(closeButton).toBeEnabled();
  await page.screenshot({
    path: testInfo.outputPath("guided-clarification-interruptible-start.png"),
    fullPage: true,
  });
  await closeButton.click();

  await expect(panel).toHaveCount(0);
  await expect(questionInput).toHaveValue(REQUIREMENT_QUESTION);
  expect(payloads.create).toBeUndefined();
  expect(payloads.clarificationAnswer).toBeUndefined();
});

test("AI要件確認は AI の推測を確認済みの一覧から直せる", async ({ page }, testInfo) => {
  const payloads = await mockApi(page, { answerStage: "corrected" });
  await page.goto("/query");
  const panel = await startRequirementCheck(page);

  const selectGroup = panel.getByTestId("nl2sql-requirement-group-select");
  await selectGroup.getByRole("button", { name: "地域と受注金額の合計を直す" }).click();
  const question = panel.getByTestId("nl2sql-clarification-question");
  await expect(question).toHaveAttribute("data-item-key", "select_region_total");
  await expect(question.getByText("直す項目: 地域と受注金額の合計")).toBeVisible();
  await expect(question.getByText("今の内容: CUSTOMERS.REGION と SUM(ORDERS.AMOUNT)")).toBeVisible();
  await expect(question.getByRole("button", { name: "直すのをやめる" })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("requirement-correct.png"), fullPage: true });

  // やめると、もとの質問（期間）に戻る。
  await question.getByRole("button", { name: "直すのをやめる" }).click();
  await expect(question).toHaveAttribute("data-item-key", "period_this_fiscal_year");

  await selectGroup.getByRole("button", { name: "地域と受注金額の合計を直す" }).click();
  await question.getByLabel("その他（自由入力）").fill("CUSTOMERS.REGION と AVG(ORDERS.AMOUNT)");
  await question.getByRole("button", { name: "直した内容で更新" }).click();
  expect(payloads.clarificationAnswer).toEqual({
    base_version: 1,
    item_id: "item-select",
    selected_candidate_ids: [],
    free_text: "CUSTOMERS.REGION と AVG(ORDERS.AMOUNT)",
  });
  const corrected = selectGroup.getByTestId("nl2sql-requirement-item");
  await expect(corrected).toHaveAttribute("data-item-status", "answered");
  await expect(corrected).toContainText("CUSTOMERS.REGION と AVG(ORDERS.AMOUNT)");
  await expect(corrected.getByText("回答済み")).toBeVisible();
});

test("AI要件確認は複数選択の質問を Checkbox で答える", async ({ page }) => {
  const payloads = await mockApi(page, { createStage: "multi_select" });
  await page.goto("/query");
  const panel = await startRequirementCheck(page);

  const question = panel.getByTestId("nl2sql-clarification-question");
  await expect(question.getByRole("heading", { name: "どの受注状態（ORDERS.STATUS）に絞りますか？" })).toBeVisible();
  await expect(question.getByRole("checkbox")).toHaveCount(2);
  await question.getByRole("checkbox", { name: /出荷済み/ }).check();
  await question.getByRole("checkbox", { name: /取消/ }).check();
  await expect(question.getByText("根拠: 列のコメント").first()).toBeVisible();
  await question.getByRole("button", { name: "この内容で回答" }).click();
  expect(payloads.clarificationAnswer).toMatchObject({
    item_id: "item-status",
    selected_candidate_ids: ["candidate-shipped", "candidate-cancelled"],
  });
});

test("AI要件確認は再検査の上限で残った項目を示し、残りを AI に任せて反映できる", async ({ page }, testInfo) => {
  const payloads = await mockApi(page, { answerStage: "limit" });
  await page.goto("/query");
  const panel = await startRequirementCheck(page);
  const question = panel.getByTestId("nl2sql-clarification-question");
  await question.getByRole("radio", { name: /2026-04-01〜2027-03-31/ }).check();
  await question.getByRole("button", { name: "この内容で回答" }).click();

  await expect(panel.getByText("AI の再検査は上限（3 回）に達しました。残った確認の項目: 金額の税", { exact: false })).toBeVisible();
  await expect(question).toHaveAttribute("data-item-key", "other_currency");
  // 残りに答えるか、SELECT と FROM が明確なので残りを AI に任せて反映するかを選べる。
  const applyRemaining = question.getByRole("button", { name: "残りを AI に任せて反映" });
  await expect(applyRemaining).toBeEnabled();
  await expect(panel.getByTestId("nl2sql-clarification-skeleton")).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("requirement-limit.png"), fullPage: true });
  await applyRemaining.click();
  await expect(panel).toHaveCount(0);
  expect(payloads.apply).toEqual({ base_version: 2 });
});

test("AI要件確認は SELECT と FROM が明確になるまで反映させない", async ({ page }, testInfo) => {
  await mockApi(page, { createStage: "minimum" });
  await page.goto("/query");
  const panel = await startRequirementCheck(page, "いい感じに出して");

  await expect(
    panel.getByText("SELECT（何を表示するか）と FROM（どの表から）が明確になるまで、SQL の生成に進めません。")
  ).toBeVisible();
  const question = panel.getByTestId("nl2sql-clarification-question");
  await expect(question.getByRole("checkbox", { name: /APP\.ORDERS（受注）/ })).toBeVisible();
  await expect(panel.getByRole("button", { name: "要件を SQL の生成に反映" })).toHaveCount(0);
  await expect(panel.getByRole("button", { name: "残りを AI に任せて反映" })).toHaveCount(0);
  await page.screenshot({ path: testInfo.outputPath("requirement-minimum.png"), fullPage: true });
});

// 375px ではサイドナビが畳まれるため、ナビのリンクと同じ client-side の遷移を history で起こす。
async function navigateInApp(page: Page, path: string) {
  await page.evaluate((target) => {
    window.history.pushState({}, "", target);
    window.dispatchEvent(new PopStateEvent("popstate"));
  }, path);
  await expect(page).toHaveURL(new RegExp(`${path}$`));
}

test("AI要件確認は別の画面へ移って戻ると同じ確認を続けられ、確認中は質問を変えられない", async ({ page }) => {
  const payloads = await mockApi(page);
  await page.goto("/query");

  const questionInput = page.locator("#nl2sql-question-input");
  const panel = await startRequirementCheck(page);
  const heading = panel.getByRole("heading", { name: "「今年度」は、どの期間ですか？" });
  await expect(heading).toBeVisible();
  // 確認は開始時の質問で進むため、確認中は質問の入力と「新しい質問を開始」を使えない。
  await expect(questionInput).toBeDisabled();
  await expect(page.getByRole("button", { name: "新しい質問を開始" })).toBeDisabled();

  // SQL 生成の画面は keep-alive。別の画面へ移っても確認は中止せず、戻ると続きから答えられる。
  await navigateInApp(page, "/chat");
  await expect(panel).toBeHidden();
  await navigateInApp(page, "/query");
  await expect(heading).toBeVisible();
  await expect(questionInput).toHaveValue(REQUIREMENT_QUESTION);
  expect(payloads.cancel).toBeUndefined();
});

test("AI要件確認は作成の途中で閉じても、作成された確認をサーバーで中止する", async ({ page }) => {
  const payloads = await mockApi(page, { guidedStartDelayMs: 1_000 });
  await page.goto("/query");

  const panel = await startRequirementCheck(page);
  // 作成の request がサーバーへ届いた後（応答の前）に閉じる。
  await expect.poll(() => payloads.create, { timeout: 10_000 }).toBeDefined();
  await panel.getByRole("button", { name: "確認を中止して閉じる" }).click();
  await expect(panel).toHaveCount(0);
  await expect.poll(() => payloads.cancel, { timeout: 10_000 }).toBe(true);
});

test("AI要件確認の途中で再読込するとサーバーの確認を中止する", async ({ page }) => {
  const payloads = await mockApi(page);
  // 再読込の途中（pagehide）の keepalive の request は、Playwright の request の event にも route にも
  // 現れない（Playwright 1.64 の Chromium では毎回。以前も route に届かないことがあった。#1667）。
  // 画面が中止を送ったことは、fetch の呼び出しを再読込をまたいで残る sessionStorage に記録して確かめる。
  await page.addInitScript(() => {
    const originalFetch = window.fetch.bind(window);
    window.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
      const url = input instanceof Request ? input.url : String(input);
      if (url.includes("/query-sessions/")) {
        const method = init?.method ?? (input instanceof Request ? input.method : "GET");
        const calls = JSON.parse(sessionStorage.getItem("e2e-query-session-fetches") ?? "[]") as unknown[];
        calls.push({ url, method, keepalive: init?.keepalive === true });
        sessionStorage.setItem("e2e-query-session-fetches", JSON.stringify(calls));
      }
      return originalFetch(input, init);
    };
  });
  await page.goto("/query");

  const panel = await startRequirementCheck(page);
  await expect(panel.getByRole("heading", { name: "「今年度」は、どの期間ですか？" })).toBeVisible();
  expect(payloads.cancel).toBeUndefined();

  await page.reload();
  const cancelCalls = await page.evaluate(
    (sessionId) =>
      (JSON.parse(sessionStorage.getItem("e2e-query-session-fetches") ?? "[]") as {
        url: string;
        method: string;
        keepalive: boolean;
      }[]).filter((call) => call.url.endsWith(`/query-sessions/${sessionId}/cancel`)),
    REQUIREMENT_SESSION_ID
  );
  expect(cancelCalls).toEqual([expect.objectContaining({ method: "POST", keepalive: true })]);
});

test("AI要件確認は期限切れで終わった確認の質問を出さず、理由と閉じるだけを出す", async ({ page }, testInfo) => {
  const payloads = await mockApi(page);
  const expiredMessage =
    "一定時間操作がなかったため、この確認は終了しました。閉じてから、もう一度始めてください。";
  // mockApi より後に登録した route が優先される。
  await page.route("**/api/nl2sql/query-sessions/*/clarification-answers", (route) =>
    route.fulfill({
      status: 409,
      contentType: "application/json",
      body: JSON.stringify({ detail: { code: "QUERY_SESSION_EXPIRED", message_ja: expiredMessage } }),
    })
  );
  await page.route(/\/api\/nl2sql\/query-sessions\/[^/]+$/, async (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    const data = requirementSessionData("needs_answer");
    Object.assign(data.session, {
      status: "cancelled",
      error_code: "QUERY_SESSION_EXPIRED",
      error_message_ja: expiredMessage,
    });
    await fulfill(route, data);
  });
  await page.goto("/query");

  const panel = await startRequirementCheck(page);
  await panel.getByRole("radio", { name: /2026-04-01〜2027-03-31/ }).check();
  await panel.getByRole("button", { name: "この内容で回答" }).click();

  await expect(panel.getByText(expiredMessage)).toHaveCount(1);
  await expect(panel.getByRole("heading", { name: "「今年度」は、どの期間ですか？" })).toHaveCount(0);
  await expect(panel.getByRole("button", { name: "この内容で回答" })).toHaveCount(0);
  await expect(panel.getByRole("button", { name: "要件を SQL の生成に反映" })).toHaveCount(0);
  await page.screenshot({ path: testInfo.outputPath("guided-clarification-expired.png"), fullPage: true });

  // 終了済みの確認は、中止を送らずに閉じる。
  await panel.getByRole("button", { name: "確認を中止して閉じる" }).click();
  await expect(panel).toHaveCount(0);
  expect(payloads.cancel).toBeUndefined();
});

// AI要件確認の LLM の失敗の応答（backend の problem と error_details。2026-10-13 の xai.grok-4.3 の HTTP 500 の形。#1715）。
const REQUIREMENT_LLM_FAILURE = {
  status: 503,
  body: {
    data: null,
    error_messages: [
      "AI要件確認で OCI Enterprise AI がサーバーのエラーを返しました（HTTP 500、2 回試行）。サービス側の一時的な障害の可能性があります。",
    ],
    error_code: "REQUIREMENT_AI_FAILED",
    problem: {
      type: "urn:nl2sql:problem:REQUIREMENT_AI_FAILED",
      title: "Service Unavailable",
      status: 503,
      detail: "AI要件確認で OCI Enterprise AI がサーバーのエラーを返しました（HTTP 500、2 回試行）。",
      code: "REQUIREMENT_AI_FAILED",
      request_id: "req-requirement-1",
      retryable: true,
      field_errors: [],
    },
    error_details: {
      kind: "unavailable",
      kind_label_ja: "サービスの一時的な失敗（5xx）",
      error_code: "ENTERPRISE_AI_UNAVAILABLE",
      http_status: 500,
      model_id: "xai.grok-4.3",
      elapsed_ms: 28350,
      attempts: 2,
      upstream_request_id: "opc-req-500",
      provider_message: "Internal server error (internal_error)",
    },
  },
};

test("AI要件確認は確認の要らない質問では聞かずに反映する（チャットと同じ流れ。#1715）", async ({ page }) => {
  const payloads = await mockApi(page, { createStage: "clear" });
  await page.goto("/query");
  const panel = await startRequirementCheck(page, "社員の一覧");
  // 最低限の要件（SELECT・FROM）が決まり、聞くことが無い: 質問を出さずに反映して閉じる。
  await expect(panel).toHaveCount(0);
  expect(payloads.apply).toEqual({ base_version: 1 });
  await expect(page.locator("#nl2sql-question-input")).toHaveValue(REQUIREMENT_CLARIFIED_QUESTION);
  await expect(page.getByTestId("nl2sql-requirement-active")).toBeVisible();
  expect(payloads.clarificationAnswer).toBeUndefined();
});

test("AI要件確認は SQL の文の質問では押せず、確認が要らない理由を出す (#1715)", async ({ page }) => {
  const payloads = await mockApi(page);
  await page.goto("/query");
  await page.locator("#nl2sql-question-input").fill("select * from orders;");
  const start = page.getByRole("button", { name: "AI要件確認", exact: true });
  await expect(start).toBeDisabled();
  await expect(page.getByTestId("nl2sql-guided-sql-input")).toHaveText(
    "質問が SQL の文なので、AI要件確認は要りません。そのまま SQL を生成できます。"
  );
  await expect(start).toHaveAttribute("aria-describedby", "nl2sql-guided-sql-input");
  // SQL の語で始まる日本語の質問は SQL の文として扱わない（チャットと同じ判定）。
  await page.locator("#nl2sql-question-input").fill("select * from orders の件数を出して");
  await expect(start).toBeEnabled();
  await expect(page.getByTestId("nl2sql-guided-sql-input")).toHaveCount(0);
  expect(payloads.create).toBeUndefined();
});

test("AI要件確認の検査の失敗は理由と詳細を出し、確認せずに SQL を生成できる (#1715)", async ({ page }, testInfo) => {
  const payloads = await mockApi(page, { createFailure: REQUIREMENT_LLM_FAILURE });
  await page.goto("/query");
  const panel = await startRequirementCheck(page);
  const error = panel.getByTestId("nl2sql-guided-clarification-error");
  // チャットと同じ部品（理由・「詳細」・もう一度確認・確認せずに SQL を生成・中止）。
  await expect(error).toContainText("サーバーのエラーを返しました（HTTP 500、2 回試行）");
  await expect(error).toContainText("もう一度確認するか、確認せずに SQL を生成してください。");
  await expect(error).toContainText("xai.grok-4.3");
  await expect(error).toContainText("opc-req-500");
  await expect(error).toContainText("Internal server error (internal_error)");
  await expect(error).toContainText("req-requirement-1");
  await expect(error).toContainText("28.4 秒");
  await expectNoPageOverflow(page);
  await error.screenshot({ path: testInfo.outputPath("guided-clarification-failure.png") });

  await error.getByRole("button", { name: "もう一度確認" }).click();
  await expect.poll(() => payloads.createFailures).toBe(2);
  await expect(error).toBeVisible();

  await error.getByRole("button", { name: "確認せずに SQL を生成" }).click();
  await expect(panel).toHaveCount(0);
  await expect.poll(() => payloads.job).toMatchObject({ question: REQUIREMENT_QUESTION });
  expect(payloads.job).not.toHaveProperty("requirement_session_id");
});

test("AI要件確認の中止は主操作の右側から元の質問を保って閉じる", async ({ page }) => {
  const payloads = await mockApi(page);
  await page.goto("/query");

  const questionInput = page.locator("#nl2sql-question-input");
  const panel = await startRequirementCheck(page);
  const closeButton = panel
    .getByTestId("nl2sql-clarification-question")
    .getByRole("button", { name: "確認を中止して閉じる" });
  await closeButton.click();

  await expect(panel).toHaveCount(0);
  await expect(questionInput).toHaveValue(REQUIREMENT_QUESTION);
  expect(payloads.cancel).toBe(true);
  expect(payloads.clarificationAnswer).toBeUndefined();
});

for (const storageBlocked of [false, true]) {
test(`ALLの要件確認はProfileと質問を引き継ぐ（保存不可=${storageBlocked}）`, async ({ page }, testInfo) => {
  const payloads = await mockApi(page);
  const allProfile = {
    ...profile,
    id: "all",
    name: "ALL",
    category: "ALL",
    description: "全業務プロファイルから候補を選択します。",
  };
  await page.route("**/api/nl2sql/profiles/search**", async (route) => {
    return fulfill(route, {
      items: [allProfile, profile].map((item) => ({
        ...item,
        allowed_table_count: item.allowed_tables.length,
        allowed_view_count: item.allowed_views.length,
        glossary_count: Object.keys(item.glossary).length,
        few_shot_count: item.few_shot_examples.length,
        version: 1,
        etag: `${item.id}-etag`,
        updated_at: "2026-07-11T00:00:00Z",
      })),
      next_cursor: null,
      total: 2,
      change_token: 1,
    });
  });
  await page.route("**/api/nl2sql/profiles/all/usage-context", async (route) => {
    return fulfill(route, {
      ...allProfile,
      object_scope_version: 1,
      version: 1,
      etag: "all-etag",
      updated_at: "2026-07-11T00:00:00Z",
    });
  });
  await page.route("**/api/nl2sql/ontology/profile-recommendations", async (route) => {
    return fulfill(route, {
      recommendation: {
        id: "recommendation-all",
        question_hash: "b".repeat(64),
        ontology_revision_id: "revision-1",
        candidates: [
          {
            profile_id: "default",
            profile_name: "標準プロファイル",
            ontology_revision_id: "revision-1",
            score: 0.94,
            matched_scenarios_ja: ["受注分析"],
            matched_terms: ["受注"],
            reasons_ja: ["受注分析の業務定義に一致しました。"],
          },
          {
            profile_id: "all",
            profile_name: "ALL",
            ontology_revision_id: "revision-1",
            score: 0.52,
            matched_scenarios_ja: [],
            matched_terms: [],
            reasons_ja: ["全体として利用できます。"],
          },
        ],
        expires_at: "2026-07-19T12:15:00Z",
      },
    });
  });
  await page.route("**/ontology/profile-recommendations/recommendation-all/confirm", async (route) => {
    const confirmation = route.request().postDataJSON();
    payloads.profileConfirmation = confirmation;
    return fulfill(route, {
      recommendation: {
        id: "recommendation-all",
        question_hash: "b".repeat(64),
        ontology_revision_id: "revision-1",
        candidates: [],
        selected_profile_id: "default",
        selected_revision_id: "revision-1",
        expires_at: "2026-07-19T12:15:00Z",
      },
      confirmation_token: "profile-confirmation-token",
    });
  });

  await page.goto("/query");
  await chooseSelectFieldOption(page.locator("#nl2sql-profile-select"), "all");
  const panel = await startRequirementCheck(page);
  await expect(panel.getByRole("group", { name: "どの業務プロファイルで確認しますか？" })).toBeVisible();
  await panel.getByRole("radio", { name: /標準プロファイル/ }).check();
  await panel.getByRole("button", { name: "このProfileで開始" }).click();
  await expect(panel.getByRole("heading", { name: "「今年度」は、どの期間ですか？" })).toBeVisible();
  expect(payloads.profileConfirmation).toMatchObject({ selected_profile_id: "default" });
  expect(payloads.create).toMatchObject({ profile_id: "default", clarification_mode: "guided" });
  await panel.getByRole("radio", { name: /2026-04-01〜2027-03-31/ }).check();
  await panel.getByRole("button", { name: "この内容で回答" }).click();
  if (storageBlocked) {
    await page.evaluate(() => {
      const original = Storage.prototype.setItem;
      Storage.prototype.setItem = function(key, value) {
        if (this === window.sessionStorage) throw new DOMException("unavailable", "QuotaExceededError");
        return original.call(this, key, value);
      };
    });
  }
  await panel.getByRole("button", { name: "要件を SQL の生成に反映" }).click();
  if (storageBlocked) {
    await expect(panel).toBeVisible();
    await expectSelectFieldValue(page.locator("#nl2sql-profile-select"), "all");
    await expect(page.locator("#nl2sql-question-input")).toHaveValue(REQUIREMENT_QUESTION);
    expect(payloads.job).toBeUndefined();
    await page.screenshot({ path: testInfo.outputPath("guided-profile-storage-error.png"), fullPage: true });
    return;
  }
  await expectSelectFieldValue(page.locator("#nl2sql-profile-select"), "default");
  await expect(page.locator("#nl2sql-question-input")).toHaveValue(REQUIREMENT_CLARIFIED_QUESTION);
  await expect(page.locator("#nl2sql-question-input")).toBeFocused();
  expect(payloads.job).toBeUndefined();
  // Profile ごとの草稿を維持し、確認した Profile の質問を再読込でも復元する。
  await chooseSelectFieldOption(page.locator("#nl2sql-profile-select"), "all");
  await expect(page.locator("#nl2sql-question-input")).toHaveValue(REQUIREMENT_QUESTION);
  await chooseSelectFieldOption(page.locator("#nl2sql-profile-select"), "default");
  await expect(page.locator("#nl2sql-question-input")).toHaveValue(REQUIREMENT_CLARIFIED_QUESTION);
  await runCurrentOntologySearch(page);
  expect(payloads.job).toMatchObject({
    profile_id: "default",
    question: REQUIREMENT_CLARIFIED_QUESTION,
    requirement_session_id: REQUIREMENT_SESSION_ID,
  });
  await page.reload();
  await expectSelectFieldValue(page.locator("#nl2sql-profile-select"), "default");
  await expect(page.locator("#nl2sql-question-input")).toHaveValue(REQUIREMENT_CLARIFIED_QUESTION);
  await page.screenshot({ path: testInfo.outputPath("guided-profile-applied.png"), fullPage: true });
});
}

test("対象オブジェクトは業務プロファイル、構築と Markdown 下書きは専用の単一ページに分離される", async ({ page }, testInfo) => {
  await mockApi(page);

  // 業務プロファイル編集: 対象オブジェクト一覧は常時表示、オントロジー構築は非表示。
  await page.goto("/profiles");
  await page.getByTestId("profile-management-grid").locator("tbody tr").first().locator("td").nth(1).click();
  await expect(page.getByTestId("profile-allowed-table-list")).toBeVisible();
  await expect(page.getByTestId("profile-ontology-build")).toHaveCount(0);
  await expect(page.locator('section[aria-label="物理・業務モデル編集"]')).toHaveCount(0);

  // 旧 tab URL は正規化され、構築と Markdown 下書きが同じ専用ページに表示される。
  await page.goto("/ontology-build?profile=default&tab=model");
  await expect(page).toHaveURL(/\/ontology-build\?profile=default$/);
  await page.getByRole("button", { name: "情報を取得", exact: true }).click();
  await expect(page.getByTestId("profile-ontology-build")).toBeVisible();
  await expect(page.getByTestId("ontology-build-markdown")).toBeVisible();
  await expect(page.locator('section[aria-label="物理・業務モデル編集"]')).toHaveCount(0);
  await expect(page.getByText("Inspector", { exact: true })).toHaveCount(0);
  await page.screenshot({ path: testInfo.outputPath("ontology-build.png"), fullPage: true });

  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  expect(overflow).toBe(false);
});

test("旧モデル編集 UI は表示せず Markdown 下書きを唯一の編集入口にする", async ({
  page,
}) => {
  await mockApi(page);
  await page.goto("/ontology-build?profile=default&tab=model");
  await expect(page).toHaveURL(/\/ontology-build\?profile=default$/);

  await page.getByRole("button", { name: "情報を取得", exact: true }).click();
  await expect(page.getByTestId("ontology-build-markdown")).toBeVisible();
  await expect(page.getByRole("tab", { name: "Markdown オントロジー下書き" })).toBeVisible();
  await expect(page.getByTestId("ontology-markdown-draft-editor")).toBeVisible();
  await expect(page.locator('section[aria-label="物理・業務モデル編集"]')).toHaveCount(0);
  await expect(page.getByLabel("Inspector の編集対象")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Draft を保存", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "意味定義を Draft に保存" })).toHaveCount(0);
  await expect(page.getByRole("region", { name: "質問のオントロジー接地確認" })).toBeVisible();
});
