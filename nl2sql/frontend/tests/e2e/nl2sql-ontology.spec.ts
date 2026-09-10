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

function guidedSessionData(
  ready: boolean,
  withSql = false,
  confirmed = false,
  done = false
) {
  const clarifiedQuestion =
    "今月の受注を対象に、検索結果には受注件数を表示してください。";
  const data: ReturnType<typeof sessionData> & { clarification?: unknown; preview?: unknown } = sessionData(
    ready ? (withSql ? "awaiting_sql_confirmation" : "awaiting_intent_confirmation") : "awaiting_intent_confirmation",
    withSql,
    confirmed,
    done
  );
  const guidedSession = {
    ...data.session,
    status: done ? "done" : data.session.status,
    clarification_mode: "guided",
    current_intent_version: ready ? 2 : 1,
    intents: ready
      ? [{
          ...intent,
          version: 2,
          question_effective: clarifiedQuestion,
          time_range: { relative_expression: "今月" },
        }]
      : [intent],
    clarification_turns: ready
      ? [{
          question: {
            id: "question-time-range",
            ambiguity_id: "ambiguity-time-range",
            category: "time_range",
            prompt_ja: "どの期間を対象にしますか？",
            answer_kind: "single_select",
            options: [],
            allow_free_text: true,
            blocking: true,
          },
          answer: {
            question_id: "question-time-range",
            selected_option_ids: ["option-this-month"],
            free_text: "",
          },
          intent_version: 2,
        }]
      : [],
  };
  data.session = guidedSession;
  if (withSql) {
    data.preview = {
      engine: "select_ai",
      engine_meta: { profile: "NL2SQL_DEFAULT_PROFILE" },
      sql: artifact.sql,
      executable_sql: artifact.sql,
      is_safe: true,
      row_limit: 0,
      note: "受注件数を集計します。",
      recommendations: [],
      rewritten_question: "受注件数を表示。期間は今月。",
    };
  }
  data.clarification = ready
    ? {
        status: "ready_to_confirm",
        current_question: null,
        remaining_questions: [],
        intent_summary: [
          { key: "entities", label_ja: "業務対象", value_ja: "受注", source: "user", confirmed: true },
          { key: "time_range", label_ja: "期間", value_ja: "今月", source: "user", confirmed: true },
        ],
        required_total: 1,
        required_confirmed: 1,
        missing_required: [],
        assumptions: [],
        turn_count: 1,
        manual_completion_required: false,
        can_generate_sql: true,
        schema_version: "guided_clarification_v1",
        message_ja: "SQL 生成に必要な情報を確認できました。",
      }
    : {
        status: "needs_answer",
        current_question: {
          id: "question-time-range",
          ambiguity_id: "ambiguity-time-range",
          category: "time_range",
          prompt_ja: "どの期間を対象にしますか？",
          reason_ja: "期間が未指定の集計は、期待と異なる範囲を集計する可能性があります。",
          answer_kind: "single_select",
          options: [
            {
              id: "option-this-month",
              label_ja: "今月",
              structured_value: { relative_expression: "今月" },
              source: "default",
              evidence_ja: "日付条件へ変換します。",
            },
          ],
          allow_free_text: true,
          blocking: true,
        },
        remaining_questions: [],
        intent_summary: [
          { key: "entities", label_ja: "業務対象", value_ja: "受注", source: "user", confirmed: true },
        ],
        required_total: 1,
        required_confirmed: 0,
        missing_required: ["集計対象の期間を確認してください。"],
        assumptions: [],
        turn_count: 0,
        manual_completion_required: false,
        can_generate_sql: false,
        schema_version: "guided_clarification_v1",
        message_ja: "SQL を正しく生成するため、必要な条件を確認します。",
      };
  return data;
}

function guidedOutputSessionData(ready: boolean) {
  const data = guidedSessionData(ready);
  const originalQuestion = "受注情報";
  const clarifiedQuestion =
    "受注を対象に、検索結果には受注状態、受注IDを表示してください。";
  data.session.original_question = originalQuestion;
  data.session.intents = data.session.intents.map((item) => ({
    ...item,
    question_original: originalQuestion,
    question_effective: ready ? clarifiedQuestion : originalQuestion,
  }));
  const outputQuestion = {
    id: "question-output-columns",
    ambiguity_id: "ambiguity-output-columns",
    category: "output",
    prompt_ja: "検索結果に表示する項目を選んでください。",
    reason_ja:
      "クエリだけでは必要な表示項目を絞れませんでした。必要な項目をすべて選んでください。",
    answer_kind: "multi_select",
    options: [
      {
        id: "option-order-status",
        label_ja: "受注状態",
        description_ja: "検索結果に「受注状態」を表示します。",
        source: "ontology",
        evidence_ja: "APP.ORDERS.STATUS",
      },
      {
        id: "option-order-id",
        label_ja: "受注ID",
        description_ja: "検索結果に「受注ID」を表示します。",
        source: "ontology",
        evidence_ja: "APP.ORDERS.ORDER_ID",
      },
    ],
    allow_free_text: true,
    blocking: true,
  };
  if (!ready) {
    data.clarification = {
      status: "needs_answer",
      current_question: outputQuestion,
      remaining_questions: [outputQuestion],
      intent_summary: [
        { key: "entities", label_ja: "対象", value_ja: "受注", source: "user", confirmed: true },
      ],
      required_total: 1,
      required_confirmed: 0,
      missing_required: [outputQuestion.prompt_ja],
      assumptions: [],
      turn_count: 0,
      manual_completion_required: false,
      can_generate_sql: false,
      schema_version: "guided_clarification_v1",
      message_ja: "SQL を正しく生成するため、必要な条件を確認します。",
    };
    return data;
  }
  data.clarification = {
    status: "ready_to_confirm",
    current_question: null,
    remaining_questions: [],
    intent_summary: [
      { key: "entities", label_ja: "対象", value_ja: "受注", source: "user", confirmed: true },
      {
        key: "dimensions",
        label_ja: "表示する項目",
        value_ja: "受注状態、受注ID",
        source: "user",
        confirmed: true,
      },
    ],
    required_total: 1,
    required_confirmed: 1,
    missing_required: [],
    assumptions: [],
    turn_count: 1,
    manual_completion_required: false,
    can_generate_sql: true,
    schema_version: "guided_clarification_v1",
    message_ja: "SQL 生成に必要な情報を確認できました。",
  };
  return data;
}

function guidedBusinessTargetSessionData(ready: boolean) {
  const data = guidedSessionData(ready);
  const businessTargetQuestion = {
    id: "question-business-targets",
    ambiguity_id: "ambiguity-business-targets",
    category: "business_meaning",
    prompt_ja: "どの業務対象について調べますか？",
    reason_ja: "検索対象の候補が複数あるため、意図した対象をすべて選んでください。",
    answer_kind: "multi_select",
    options: [
      {
        id: "option-orders",
        label_ja: "受注",
        description_ja: "「受注」を検索対象として扱います。",
        source: "ontology",
        evidence_ja: "APP.ORDERS",
      },
      {
        id: "option-customers",
        label_ja: "顧客",
        description_ja: "「顧客」を検索対象として扱います。",
        source: "ontology",
        evidence_ja: "APP.CUSTOMERS",
      },
    ],
    allow_free_text: true,
    blocking: true,
  };
  if (!ready) {
    data.clarification = {
      status: "needs_answer",
      current_question: businessTargetQuestion,
      remaining_questions: [businessTargetQuestion],
      intent_summary: [],
      required_total: 1,
      required_confirmed: 0,
      missing_required: [businessTargetQuestion.prompt_ja],
      assumptions: [],
      turn_count: 0,
      manual_completion_required: false,
      can_generate_sql: false,
      schema_version: "guided_clarification_v1",
      message_ja: "SQL を正しく生成するため、必要な条件を確認します。",
    };
  }
  return data;
}

function guidedInferredTargetSessionData(ready: boolean) {
  const data = guidedSessionData(ready);
  const originalQuestion = "一覧を表示";
  const targetQuestion = {
    id: "question-confirm-inferred-target",
    summary_key: "entities",
    category: "business_meaning",
    prompt_ja: "検索対象は「部署」で合っていますか？",
    reason_ja:
      "AI がクエリから補った解釈です。内容を確認し、異なる場合は正しい条件を入力してください。",
    answer_kind: "single_select",
    options: [
      {
        id: "option-confirm-inferred-target",
        label_ja: "はい、この内容で進める",
        description_ja: "部署",
        source: "ontology",
        evidence_ja: "physical_67c53135ab4290e53f2e7fef",
      },
    ],
    allow_free_text: true,
    blocking: true,
  };
  data.session.original_question = originalQuestion;
  data.session.intents = data.session.intents.map((item) => ({
    ...item,
    question_original: originalQuestion,
    question_effective: originalQuestion,
    entities: [
      {
        id: "intent-department",
        ontology_node_id: "business_entity_9531b8867ab66ed5de76d7bd",
        name_ja: "部署",
        role: "subject",
        physical_object_ids: ["physical_67c53135ab4290e53f2e7fef"],
      },
    ],
    metrics: [],
    time_range: null,
  }));
  const inferredSession = {
    ...data.session,
    current_intent_version: ready ? 2 : 1,
    clarification_turns: ready
      ? [{
          question: targetQuestion,
          answer: {
            question_id: targetQuestion.id,
            selected_option_ids: [targetQuestion.options[0].id],
            free_text: "",
          },
          intent_version: 2,
        }]
      : [],
  };
  data.session = inferredSession;
  data.clarification = ready
    ? {
        status: "ready_to_confirm",
        current_question: null,
        remaining_questions: [],
        intent_summary: [
          { key: "entities", label_ja: "対象", value_ja: "部署", source: "user", confirmed: true },
        ],
        required_total: 1,
        required_confirmed: 1,
        missing_required: [],
        assumptions: [],
        turn_count: 1,
        manual_completion_required: false,
        can_generate_sql: true,
        schema_version: "guided_clarification_v1",
        message_ja: "SQL 生成に必要な情報を確認できました。",
      }
    : {
        status: "needs_answer",
        current_question: targetQuestion,
        remaining_questions: [targetQuestion],
        intent_summary: [
          {
            key: "entities",
            label_ja: "対象",
            value_ja: "部署",
            source: "ontology",
            confirmed: false,
            technical_evidence_ja:
              "business_entity_9531b8867ab66ed5de76d7bd, physical_67c53135ab4290e53f2e7fef",
          },
        ],
        required_total: 1,
        required_confirmed: 0,
        missing_required: [targetQuestion.prompt_ja],
        assumptions: [],
        turn_count: 0,
        manual_completion_required: false,
        can_generate_sql: false,
        schema_version: "guided_clarification_v1",
        message_ja: "SQL を正しく生成するため、必要な条件を確認します。",
      };
  return data;
}

function guidedLimitSessionData(withSql = false, confirmed = false, executed = false) {
  const data = guidedSessionData(true, withSql, confirmed, executed);
  const question = "受注件数を上位 10 件表示";
  const limitedIntent = {
    ...intent,
    question_original: question,
    question_effective: question,
    limit: 10,
  };
  const limitedArtifact = {
    ...artifact,
    sql: "SELECT COUNT(*) AS ORDER_COUNT FROM APP.ORDERS FETCH FIRST 10 ROWS ONLY",
    semantic_graph: {
      ...artifact.semantic_graph,
      raw_sql: "SELECT COUNT(*) AS ORDER_COUNT FROM APP.ORDERS FETCH FIRST 10 ROWS ONLY",
      limit: 10,
    },
  };
  data.session.original_question = question;
  data.session.intents = [limitedIntent];
  data.session.sql_artifacts = withSql ? [limitedArtifact] : [];
  data.session.current_sql_artifact_id = withSql ? limitedArtifact.id : null;
  data.preview = withSql
    ? {
        engine: "select_ai",
        engine_meta: { profile: "NL2SQL_DEFAULT_PROFILE" },
        sql: limitedArtifact.sql,
        executable_sql: limitedArtifact.sql,
        is_safe: true,
        row_limit: 10,
        note: "受注件数を集計します。",
        recommendations: [],
        rewritten_question: question,
      }
    : null;
  data.clarification = {
    status: "ready_to_confirm",
    current_question: null,
    remaining_questions: [],
    intent_summary: [
      { key: "entities", label_ja: "業務対象", value_ja: "受注", source: "ontology", confirmed: false },
      { key: "limit", label_ja: "最大件数", value_ja: "10 件", source: "user", confirmed: true },
    ],
    required_total: 0,
    required_confirmed: 0,
    missing_required: [],
    assumptions: [],
    turn_count: 0,
    manual_completion_required: false,
    can_generate_sql: true,
    schema_version: "guided_clarification_v1",
    message_ja: "SQL 生成に必要な情報を確認できました。",
  };
  return data;
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
}

async function waitForMockDelay(delayMs: number) {
  if (delayMs <= 0) return;
  await new Promise((resolve) => setTimeout(resolve, delayMs));
}

async function mockApi(
  page: Page,
  guidedQuestion: "time" | "output" | "business" | "inferred" = "time",
  options: MockApiOptions = {},
) {
  const payloads: Record<string, unknown> = {};
  const guidedStartDelayMs = options.guidedStartDelayMs ?? 0;
  const isGuidedLimitRequest = () =>
    String((payloads.create as { question?: unknown } | undefined)?.question ?? "").includes(
      "上位 10"
    );
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    // 共通 helper の認証 mock を優先し、catch-all で CurrentUser を空 object にしない。
    if (path === "/api/auth/me") return route.fallback();
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
        if (isGuidedLimitRequest()) return fulfill(route, guidedLimitSessionData());
        const guidedData = guidedQuestion === "output"
          ? guidedOutputSessionData(false)
          : guidedQuestion === "business"
            ? guidedBusinessTargetSessionData(false)
            : guidedQuestion === "inferred"
              ? guidedInferredTargetSessionData(false)
              : guidedSessionData(false);
        return fulfill(route, guidedData);
      }
      return fulfill(route, sessionData("awaiting_intent_confirmation"));
    }
    if (path.endsWith("/clarification-answers") && request.method() === "POST") {
      payloads.clarificationAnswer = request.postDataJSON();
      const guidedData = guidedQuestion === "output"
        ? guidedOutputSessionData(true)
        : guidedQuestion === "business"
          ? guidedBusinessTargetSessionData(true)
          : guidedQuestion === "inferred"
            ? guidedInferredTargetSessionData(true)
            : guidedSessionData(true);
      return fulfill(route, guidedData);
    }
    if (path.endsWith("/cancel") && request.method() === "POST") {
      payloads.cancel = true;
      const data = guidedSessionData(false);
      data.session.status = "cancelled";
      return fulfill(route, data);
    }
    if (path.endsWith("/intent") && request.method() === "PATCH") {
      payloads.patch = request.postDataJSON();
      return fulfill(route, sessionData("awaiting_intent_confirmation"));
    }
    if (path.endsWith("/generate-sql")) {
      payloads.generate = request.postDataJSON();
      if ((payloads.create as { clarification_mode?: string } | undefined)?.clarification_mode === "guided") {
        if (isGuidedLimitRequest()) return fulfill(route, guidedLimitSessionData(true));
        return fulfill(route, guidedSessionData(true, true));
      }
      return fulfill(route, sessionData("awaiting_sql_confirmation", true));
    }
    if (path.endsWith("/confirm-sql")) {
      payloads.confirm = request.postDataJSON();
      if ((payloads.create as { clarification_mode?: string } | undefined)?.clarification_mode === "guided") {
        if (isGuidedLimitRequest()) return fulfill(route, guidedLimitSessionData(true, true));
        return fulfill(route, guidedSessionData(true, true, true));
      }
      return fulfill(route, sessionData("awaiting_sql_confirmation", true, true));
    }
    if (path.endsWith("/execute")) {
      payloads.execute = request.postDataJSON();
      if ((payloads.create as { clarification_mode?: string } | undefined)?.clarification_mode === "guided") {
        if (isGuidedLimitRequest()) return fulfill(route, guidedLimitSessionData(true, true, true));
        return fulfill(route, guidedSessionData(true, true, true, true));
      }
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
  await page.getByRole("button", { name: "検索を実行" }).click();
  await expect(page.getByRole("columnheader", { name: "ORDER_COUNT" })).toBeVisible();
}

test("検索を実行すると公開済みオントロジー context を使って結果と解釈を表示する", async ({ page }, testInfo) => {
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

test("検索実行は明示操作後だけ現在の質問とオントロジー利用設定を送信する", async ({ page }, testInfo) => {
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

test("AI要件確認は一問ずつ確認した内容でクエリを置き換える", async ({ page }, testInfo) => {
  const payloads = await mockApi(page);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/query");

  await expect(
    page.getByText(
      "AIによるSQL生成の精度を高めるため、対話を通じてクエリの対象・表示項目・条件を補い、より明確で具体的な内容に整えます。"
    )
  ).toBeVisible();
  await page.locator("#nl2sql-question-input").fill("受注件数を表示");
  await page.getByRole("button", { name: "AI要件確認" }).click();

  const panel = page.getByTestId("nl2sql-guided-clarification");
  await expect(panel).toBeVisible();
  const questionHeading = panel.getByRole("heading", { name: "どの期間を対象にしますか？" });
  await expect(questionHeading).toBeFocused();
  const nextButton = panel.getByRole("button", { name: "選んだ内容で次へ" });
  const answerActions = nextButton.locator("..");
  await expect(answerActions.getByRole("button")).toHaveCount(2);
  await expect(answerActions.getByRole("button").nth(0)).toHaveText("選んだ内容で次へ");
  await expect(answerActions.getByRole("button").nth(1)).toHaveText("確認を中止して閉じる");
  await panel.getByRole("radio", { name: /今月/ }).check();
  await nextButton.click();

  await expect(panel.getByText("確認完了").first()).toBeVisible();
  await expect(panel.getByRole("heading", { name: "確認したクエリ内容" })).toBeVisible();
  await expect(panel.getByText("今月", { exact: true })).toBeVisible();
  await expect(panel.getByText("最大件数")).toHaveCount(0);
  await expect(panel.getByText("結果は最大 100 件に制限します。")).toHaveCount(0);
  await panel.getByRole("button", { name: "確認内容をクエリに反映" }).click();

  const clarifiedQuestion =
    "今月の受注を対象に、検索結果には受注件数を表示してください。";
  const questionInput = page.locator("#nl2sql-question-input");
  await expect(panel).toHaveCount(0);
  await expect(questionInput).toHaveValue(clarifiedQuestion);
  await expect(questionInput).toBeFocused();
  await expect(page.getByText("確認内容をクエリに反映しました。内容を確認して検索を実行してください。")).toBeVisible();
  expect(payloads.generate).toBeUndefined();
  expect(payloads.confirm).toBeUndefined();
  expect(payloads.execute).toBeUndefined();
  expect(payloads.create).toMatchObject({
    question: "受注件数を表示",
    profile_id: "default",
    clarification_mode: "guided",
  });
  expect(payloads.clarificationAnswer).toMatchObject({
    base_version: 1,
    question_id: "question-time-range",
    selected_option_ids: ["option-this-month"],
  });
  await runCurrentOntologySearch(page);
  expect(payloads.job).toMatchObject({ question: clarifiedQuestion });
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  expect(overflow).toBe(false);
  await page.screenshot({ path: testInfo.outputPath("guided-clarification.png"), fullPage: true });
});

test("AI要件確認の開始中は実処理に合わせて案内を切り替え経過時間を表示する", async ({ page }, testInfo) => {
  await mockApi(page, "time", { guidedStartDelayMs: 1_500 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/query");

  await page.locator("#nl2sql-question-input").fill("受注件数を表示");
  await page.getByRole("button", { name: "AI要件確認" }).click();

  const panel = page.getByTestId("nl2sql-guided-clarification");
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
  await expect(progress.locator('[data-loading-icon="true"]')).toHaveCSS("animation-name", "none");

  await expect(timer).toContainText(/00:0[1-9]/);
  await expect(visibleProgressLabel).toHaveText("利用する業務プロファイルを確定しています");
  await expect(visibleProgressLabel).toHaveText("クエリの確認項目を整理しています");
  await expect(timer).toContainText(/00:0[2-9]/);
  await page.screenshot({
    path: testInfo.outputPath("guided-clarification-progress.png"),
    fullPage: true,
  });

  await expect(panel.getByRole("heading", { name: "どの期間を対象にしますか？" })).toBeVisible();
  await expect(progress).toHaveCount(0);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  expect(overflow).toBe(false);
});

test("AI要件確認は開始処理の途中でも中止して閉じられる", async ({ page }, testInfo) => {
  const payloads = await mockApi(page, "time", { guidedStartDelayMs: 1_500 });
  await page.goto("/query");

  const originalQuestion = "受注件数を表示";
  const questionInput = page.locator("#nl2sql-question-input");
  await questionInput.fill(originalQuestion);
  await page.getByRole("button", { name: "AI要件確認" }).click();

  const panel = page.getByTestId("nl2sql-guided-clarification");
  await expect(panel.getByTestId("nl2sql-guided-start-progress")).toBeVisible();
  const closeButton = panel.getByRole("button", { name: "確認を中止して閉じる" });
  await expect(closeButton).toBeEnabled();
  await page.screenshot({
    path: testInfo.outputPath("guided-clarification-interruptible-start.png"),
    fullPage: true,
  });
  await closeButton.click();

  await expect(panel).toHaveCount(0);
  await expect(questionInput).toHaveValue(originalQuestion);
  expect(payloads.create).toBeUndefined();
  expect(payloads.clarificationAnswer).toBeUndefined();
});

test("AI要件確認は利用者が明示した最大件数を保ったままクエリへ反映する", async ({ page }) => {
  const payloads = await mockApi(page);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/query");

  await page.locator("#nl2sql-question-input").fill("受注件数を上位 10 件表示");
  await page.getByRole("button", { name: "AI要件確認" }).click();

  const panel = page.getByTestId("nl2sql-guided-clarification");
  await expect(panel.getByText("確認完了").first()).toBeVisible();
  await expect(panel.getByText("最大件数")).toBeVisible();
  await expect(panel.getByText("10 件", { exact: true })).toBeVisible();
  await expect(panel.getByText("結果は最大 100 件に制限します。")).toHaveCount(0);
  await panel.getByRole("button", { name: "確認内容をクエリに反映" }).click();
  await expect(page.locator("#nl2sql-question-input")).toHaveValue("受注件数を上位 10 件表示");
  expect(payloads.generate).toBeUndefined();
  expect(payloads.confirm).toBeUndefined();
  expect(payloads.execute).toBeUndefined();
  expect(payloads.create).toMatchObject({
    question: "受注件数を上位 10 件表示",
    profile_id: "default",
    clarification_mode: "guided",
  });
});

test("AI要件確認は推測した検索対象を利用者へ質問し内部IDを表示しない", async ({ page }, testInfo) => {
  const payloads = await mockApi(page, "inferred");
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/query");

  await page.locator("#nl2sql-question-input").fill("一覧を表示");
  await page.getByRole("button", { name: "AI要件確認" }).click();

  const panel = page.getByTestId("nl2sql-guided-clarification");
  await expect(
    panel.getByRole("heading", { name: "検索対象は「部署」で合っていますか？" })
  ).toBeFocused();
  await expect(panel.getByRole("heading", { name: "確認したクエリ内容" })).toHaveCount(0);
  await expect(panel.getByText("対象", { exact: true })).toHaveCount(0);
  await expect(panel.getByText(/要確認|AIの推定/)).toHaveCount(0);
  await expect(panel.getByText("確認完了")).toHaveCount(0);
  await expect(panel.getByRole("button", { name: "確認内容をクエリに反映" })).toHaveCount(0);
  await expect(panel.getByText(/business_entity_|physical_/)).toHaveCount(0);
  await expect(panel.getByText("データ項目の詳細")).toHaveCount(0);
  await expect(panel.getByText("管理者・開発者向け")).toHaveCount(0);
  await page.screenshot({
    path: testInfo.outputPath("guided-inferred-target-pending.png"),
    fullPage: true,
  });

  await panel.getByRole("radio", { name: /はい、この内容で進める/ }).check();
  await panel.getByRole("button", { name: "選んだ内容で次へ" }).click();

  await expect(panel.getByText("確認完了").first()).toBeVisible();
  await expect(panel.getByRole("heading", { name: "確認したクエリ内容" })).toBeVisible();
  await expect(panel.getByText("対象", { exact: true })).toBeVisible();
  await expect(panel.getByText("部署", { exact: true })).toBeVisible();
  await expect(panel.getByText("確認済み", { exact: true }).first()).toBeVisible();
  await expect(panel.getByText(/要確認|AIの推定|business_entity_|physical_/)).toHaveCount(0);
  await expect(panel.getByRole("button", { name: "確認内容をクエリに反映" })).toBeEnabled();
  expect(payloads.clarificationAnswer).toMatchObject({
    question_id: "question-confirm-inferred-target",
    selected_option_ids: ["option-confirm-inferred-target"],
  });
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  expect(overflow).toBe(false);
  await page.screenshot({
    path: testInfo.outputPath("guided-inferred-target-confirmation.png"),
    fullPage: true,
  });
});

test("AI要件確認は内部診断を見せず表示項目を自然なクエリへ完全反映できる", async ({ page }, testInfo) => {
  const payloads = await mockApi(page, "output");
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/query");

  await page.locator("#nl2sql-question-input").fill("受注情報");
  await page.getByRole("button", { name: "AI要件確認" }).click();

  const panel = page.getByTestId("nl2sql-guided-clarification");
  await expect(
    panel.getByRole("heading", { name: "検索結果に表示する項目を選んでください。" })
  ).toBeFocused();
  await expect(panel.getByText(/Embedding|Ontology|Schema/)).toHaveCount(0);
  await expect(panel.getByRole("heading", { name: "確認したクエリ内容" })).toBeVisible();
  await expect(panel.getByText("確認済み", { exact: true })).toBeVisible();

  await panel.getByRole("checkbox", { name: /受注状態/ }).check();
  await panel.getByRole("checkbox", { name: /受注ID/ }).check();
  await expect(panel.getByText(/APP\.ORDERS/)).toHaveCount(0);
  await expect(panel.getByText("管理者・開発者向け")).toHaveCount(0);
  const outputNextButton = panel.getByRole("button", { name: "選んだ内容で次へ" });
  await outputNextButton.scrollIntoViewIfNeeded();
  await page.screenshot({
    path: testInfo.outputPath("guided-output-selection-question.png"),
    fullPage: true,
  });
  await outputNextButton.click();

  await expect(panel.getByText("表示する項目")).toBeVisible();
  await expect(panel.getByText("受注状態、受注ID")).toBeVisible();
  await expect(panel.getByText("確認済み", { exact: true }).first()).toBeVisible();
  expect(payloads.clarificationAnswer).toMatchObject({
    question_id: "question-output-columns",
    selected_option_ids: ["option-order-status", "option-order-id"],
  });
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  expect(overflow).toBe(false);
  await page.screenshot({ path: testInfo.outputPath("guided-output-selection.png"), fullPage: true });
  await panel.getByRole("button", { name: "確認内容をクエリに反映" }).click();
  const clarifiedQuestion =
    "受注を対象に、検索結果には受注状態、受注IDを表示してください。";
  const questionInput = page.locator("#nl2sql-question-input");
  await expect(questionInput).toHaveValue(clarifiedQuestion);
  await questionInput.scrollIntoViewIfNeeded();
  await page.screenshot({
    path: testInfo.outputPath("guided-output-natural-query.png"),
    fullPage: true,
  });
  await runCurrentOntologySearch(page);
  expect(payloads.job).toMatchObject({ question: clarifiedQuestion });
});

test("AI要件確認は複数の業務対象をCheckboxで選択できる", async ({ page }, testInfo) => {
  const payloads = await mockApi(page, "business");
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/query");

  await page.locator("#nl2sql-question-input").fill("情報を表示");
  await page.getByRole("button", { name: "AI要件確認" }).click();

  const panel = page.getByTestId("nl2sql-guided-clarification");
  await expect(
    panel.getByRole("heading", { name: "どの業務対象について調べますか？" })
  ).toBeFocused();
  await expect(panel.getByText("意図した対象をすべて選んでください。", { exact: false })).toBeVisible();
  await expect(panel.getByRole("radio")).toHaveCount(0);

  await panel.getByRole("checkbox", { name: /受注/ }).check();
  await panel.getByRole("checkbox", { name: /顧客/ }).check();
  await page.screenshot({
    path: testInfo.outputPath("guided-business-target-multiselect.png"),
    fullPage: true,
  });
  await panel.getByRole("button", { name: "選んだ内容で次へ" }).click();

  expect(payloads.clarificationAnswer).toMatchObject({
    question_id: "question-business-targets",
    selected_option_ids: ["option-orders", "option-customers"],
  });
});

test("AI要件確認の中止は主操作の右側から元のクエリを保って閉じる", async ({ page }) => {
  const payloads = await mockApi(page);
  await page.goto("/query");

  const originalQuestion = "受注件数を表示";
  const questionInput = page.locator("#nl2sql-question-input");
  await questionInput.fill(originalQuestion);
  await page.getByRole("button", { name: "AI要件確認" }).click();

  const panel = page.getByTestId("nl2sql-guided-clarification");
  const nextButton = panel.getByRole("button", { name: "選んだ内容で次へ" });
  const closeButton = panel.getByRole("button", { name: "確認を中止して閉じる" });
  await expect(nextButton.locator("..").getByRole("button")).toHaveText([
    "選んだ内容で次へ",
    "確認を中止して閉じる",
  ]);
  await closeButton.click();

  await expect(panel).toHaveCount(0);
  await expect(questionInput).toHaveValue(originalQuestion);
  expect(payloads.cancel).toBe(true);
  expect(payloads.clarificationAnswer).toBeUndefined();
});

for (const storageBlocked of [false, true]) {
test(`ALLの要件確認はProfileとクエリを引き継ぐ（保存不可=${storageBlocked}）`, async ({ page }, testInfo) => {
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
            reasons_ja: ["全体検索として利用できます。"],
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
  await page.locator("#nl2sql-profile-select").selectOption("all");
  await page.locator("#nl2sql-question-input").fill("受注件数を表示");
  await page.getByRole("button", { name: "AI要件確認" }).click();

  const panel = page.getByTestId("nl2sql-guided-clarification");
  await expect(panel.getByRole("group", { name: "どの業務プロファイルで確認しますか？" })).toBeVisible();
  await panel.getByRole("radio", { name: /標準プロファイル/ }).check();
  await panel.getByRole("button", { name: "このProfileで開始" }).click();
  await expect(panel.getByRole("heading", { name: "どの期間を対象にしますか？" })).toBeVisible();
  expect(payloads.profileConfirmation).toMatchObject({ selected_profile_id: "default" });
  expect(payloads.create).toMatchObject({ profile_id: "default", clarification_mode: "guided" });
  await panel.getByRole("radio", { name: /今月/ }).check();
  await panel.getByRole("button", { name: "選んだ内容で次へ" }).click();
  if (storageBlocked) {
    await page.evaluate(() => {
      const original = Storage.prototype.setItem;
      Storage.prototype.setItem = function(key, value) {
        if (this === window.sessionStorage) throw new DOMException("unavailable", "QuotaExceededError");
        return original.call(this, key, value);
      };
    });
  }
  await panel.getByRole("button", { name: "確認内容をクエリに反映" }).click();
  if (storageBlocked) {
    await expect(panel).toBeVisible();
    await expect(page.locator("#nl2sql-profile-select")).toHaveValue("all");
    await expect(page.locator("#nl2sql-question-input")).toHaveValue("受注件数を表示");
    expect(payloads.job).toBeUndefined();
    await expect(page.getByText("確認内容をクエリに反映しました。内容を確認して検索を実行してください。")).toHaveCount(0);
    await page.screenshot({ path: testInfo.outputPath("guided-profile-storage-error.png"), fullPage: true });
    return;
  }
  await expect(page.locator("#nl2sql-profile-select")).toHaveValue("default");
  const clarified = "今月の受注を対象に、検索結果には受注件数を表示してください。";
  await expect(page.locator("#nl2sql-question-input")).toHaveValue(clarified);
  await expect(page.locator("#nl2sql-question-input")).toBeFocused();
  expect(payloads.job).toBeUndefined();
  // Profile ごとの草稿を維持し、確認した Profile のクエリを再読込でも復元する。
  await page.locator("#nl2sql-profile-select").selectOption("all");
  await expect(page.locator("#nl2sql-question-input")).toHaveValue("受注件数を表示");
  await page.locator("#nl2sql-profile-select").selectOption("default");
  await expect(page.locator("#nl2sql-question-input")).toHaveValue(clarified);
  await page.reload();
  await expect(page.locator("#nl2sql-profile-select")).toHaveValue("default");
  await expect(page.locator("#nl2sql-question-input")).toHaveValue(clarified);
  expect(payloads.job).toBeUndefined();
  await runCurrentOntologySearch(page);
  expect(payloads.job).toMatchObject({ profile_id: "default", question: clarified });
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

  await expect(page.getByTestId("ontology-build-markdown")).toBeVisible();
  await expect(page.getByRole("tab", { name: "Markdown オントロジー下書き" })).toBeVisible();
  await expect(page.getByTestId("ontology-markdown-draft-editor")).toBeVisible();
  await expect(page.locator('section[aria-label="物理・業務モデル編集"]')).toHaveCount(0);
  await expect(page.getByLabel("Inspector の編集対象")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Draft を保存", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "意味定義を Draft に保存" })).toHaveCount(0);
  await expect(page.getByRole("region", { name: "質問のオントロジー接地確認" })).toBeVisible();
});
