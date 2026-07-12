import assert from "node:assert/strict";
import test from "node:test";

import { querySessionPath } from "../src/features/nl2sql/ontology/api.ts";
import {
  buildIntentEditorPatch,
  createIntentEditorDraft,
  intentEditorConceptFromOption,
  intentEditorFilterFromOption,
  intentEditorNodeOptions,
  intentEditorSort,
} from "../src/features/nl2sql/ontology/IntentEditorCore.ts";
import {
  boundedOntologyGraph,
  currentIntentForSession,
  currentIntentVersionForSession,
  currentSqlArtifactForSession,
  executionBindingForSession,
  hasGraphPatchVersionConflict,
  intentGraphToOntologyGraph,
  ontologyRelationshipRows,
  profileScopedOntologyGraph,
  querySessionState,
  sortOntologyRelationshipRows,
  sqlSemanticGraphToOntologyGraph,
  type OntologyGraph,
  type QuerySession,
  type QuestionIntentGraph,
  type SqlSemanticGraph,
} from "../src/features/nl2sql/ontology/types.ts";

test("ontology graph is bounded to 100 nodes and reports omitted kinds and edges", () => {
  const graph: OntologyGraph = {
    nodes: Array.from({ length: 105 }, (_, index) => ({
      id: `node-${index}`,
      kind: index >= 100 ? "metric" : "business_entity",
      business_name_ja: `ノード ${index}`,
    })),
    edges: Array.from({ length: 104 }, (_, index) => ({
      id: `edge-${index}`,
      source_node_id: `node-${index}`,
      target_node_id: `node-${index + 1}`,
      relationship_name_ja: "関連",
    })),
  };

  const visible = boundedOntologyGraph(graph, 500);

  assert.equal(visible.nodes.length, 100);
  assert.equal(visible.edges.length, 99);
  assert.equal(visible.hidden_node_count, 5);
  assert.equal(visible.hidden_edge_count, 5);
  assert.equal(visible.hidden_node_kinds.metric, 5);
});

test("relationship list renders ordered composite join conditions and sorts in Japanese", () => {
  const graph: OntologyGraph = {
    nodes: [
      { id: "order", kind: "business_entity", business_name_ja: "注文" },
      { id: "customer", kind: "business_entity", business_name_ja: "顧客" },
    ],
    edges: [
      {
        id: "order-customer",
        source_node_id: "order",
        target_node_id: "customer",
        relationship_name_ja: "購入者",
        review_status: "published",
        join_conditions: [
          { source_column: "TENANT_ID", target_column: "TENANT_ID", ordinal: 1 },
          { source_column: "CUSTOMER_ID", target_column: "ID", ordinal: 2 },
        ],
      },
    ],
  };

  const rows = ontologyRelationshipRows(graph);
  assert.equal(rows[0]?.join_condition, "TENANT_ID = TENANT_ID AND CUSTOMER_ID = ID");
  assert.equal(rows[0]?.validation_status, "passed");
  assert.equal(sortOntologyRelationshipRows(rows, "target", "desc")[0]?.target_label, "顧客");
});

test("profile ontology scope excludes otherwise approved nodes and relationships", () => {
  const graph: OntologyGraph = {
    nodes: [
      { id: "allowed", kind: "business_entity", business_name_ja: "受注", review_status: "approved" },
      { id: "outside", kind: "metric", business_name_ja: "社外秘指標", review_status: "approved" },
    ],
    edges: [
      {
        id: "outside-edge",
        source_node_id: "allowed",
        target_node_id: "outside",
        relationship_name_ja: "非公開関係",
      },
    ],
  };

  const scoped = profileScopedOntologyGraph(
    {
      id: "view-1",
      profile_id: "sales",
      ontology_revision_id: "revision-1",
      node_ids: ["allowed"],
      edge_ids: [],
      graph,
    },
    graph
  );

  assert.deepEqual(scoped.nodes.map((node) => node.id), ["allowed"]);
  assert.deepEqual(scoped.edges, []);
});

test("backend intent contract converts to a readable graph including blocker ambiguity", () => {
  const intent: QuestionIntentGraph = {
    version: 3,
    question_original: "先月の売上は？",
    question_effective: "先月の受注売上合計",
    entities: [{ id: "e1", ontology_node_id: "entity:order", name_ja: "受注", role: "target" }],
    metrics: [{ id: "m1", ontology_node_id: "metric:sales", name_ja: "売上", aggregation: "sum" }],
    dimensions: [{ id: "d1", ontology_node_id: "property:month", name_ja: "月" }],
    filters: [
      {
        id: "f1",
        property_node_id: "property:status",
        label_ja: "受注状態",
        operator: "=",
        value: "確定",
      },
    ],
    ambiguities: [
      {
        id: "a1",
        code: "time_boundary",
        message_ja: "先月の締め境界を確認してください",
        blocking: true,
        resolved: false,
      },
    ],
  };

  const graph = intentGraphToOntologyGraph(intent);

  assert.ok(graph.nodes.some((node) => node.id === "entity:order" && node.business_name_ja === "受注"));
  assert.ok(graph.nodes.some((node) => node.id === "metric:sales" && node.kind === "metric"));
  assert.ok(graph.nodes.some((node) => node.id === "a1" && node.validation_status === "blocked"));
  assert.ok(graph.edges.some((edge) => edge.relationship_name_ja === "要確認"));
});

test("Oracle SQL semantic graph converts AST elements and unreviewed join into graph", () => {
  const sqlGraph: SqlSemanticGraph = {
    version: 1,
    sql_hash: "sql-hash",
    dialect: "oracle",
    statement_type: "SELECT",
    raw_sql: "SELECT SUM(o.amount) FROM orders o JOIN customers c ON c.id=o.customer_id GROUP BY c.name",
    ctes: [],
    tables: [{ expression: "ORDERS", alias: "o" }, { expression: "CUSTOMERS", alias: "c" }],
    columns: [{ expression: "o.amount" }, { expression: "c.name" }],
    joins: [
      {
        expression: "ORDERS → CUSTOMERS",
        condition: "c.id = o.customer_id",
        join_type: "INNER",
        reviewed_path: false,
      },
    ],
    filters: [],
    aggregates: [{ expression: "SUM(o.amount)" }],
    group_by: [],
    groups: [{ expression: "c.name" }],
    having: [],
    order_by: [],
    orders: [],
    windows: [],
    limit: 100,
  };

  const graph = sqlSemanticGraphToOntologyGraph(sqlGraph);

  assert.ok(graph.nodes.some((node) => node.kind === "sql_group" && node.business_name_ja === "c.name"));
  assert.ok(graph.nodes.some((node) => node.kind === "sql_join" && node.validation_status === "blocked"));
  assert.ok(graph.nodes.some((node) => node.kind === "sql_limit" && node.business_name_ja === "上限 100 件"));
});

test("query session adapters restore current version and hash-bound execution request", () => {
  const intentV1: QuestionIntentGraph = {
    version: 1,
    entities: [],
    metrics: [],
    dimensions: [],
    filters: [],
  };
  const intentV2: QuestionIntentGraph = {
    ...intentV1,
    version: 2,
    question_effective: "確定受注の売上",
  };
  const semanticGraph: SqlSemanticGraph = {
    dialect: "oracle",
    ctes: [],
    tables: [],
    columns: [],
    joins: [],
    filters: [],
    aggregates: [],
    group_by: [],
    having: [],
    order_by: [],
    windows: [],
  };
  const session: QuerySession = {
    id: "session-1",
    profile_id: "finance",
    ontology_revision_id: "ontology-r7",
    status: "awaiting_sql_confirmation",
    current_intent_version: 2,
    intents: [intentV1, intentV2],
    current_sql_artifact_id: "artifact-2",
    sql_artifacts: [
      {
        id: "artifact-2",
        intent_version: 2,
        ontology_revision_id: "ontology-r7",
        sql: "SELECT 1 FROM DUAL",
        sql_hash: "sql-hash-2",
        generation_context_hash: "context-hash-2",
        semantic_graph: semanticGraph,
        validation_report: {
          id: "validation-2",
          is_valid: true,
          findings: [],
          intent_coverage: 1,
          validation_hash: "validation-hash-2",
        },
      },
    ],
  };

  assert.equal(querySessionState(session), "awaiting_sql_confirmation");
  assert.equal(currentIntentVersionForSession(session), 2);
  assert.equal(currentIntentForSession(session)?.question_effective, "確定受注の売上");
  assert.equal(currentSqlArtifactForSession(session)?.id, "artifact-2");
  assert.deepEqual(executionBindingForSession(session), {
    session_id: "session-1",
    artifact_id: "artifact-2",
    ontology_revision_id: "ontology-r7",
    intent_version: 2,
    sql_hash: "sql-hash-2",
    validation_hash: "validation-hash-2",
    generation_context_hash: "context-hash-2",
  });
});

test("patch conflict and query-session path helpers are deterministic", () => {
  assert.equal(hasGraphPatchVersionConflict(2, 3), true);
  assert.equal(hasGraphPatchVersionConflict(3, 3), false);
  assert.equal(querySessionPath(), "/api/nl2sql/query-sessions");
  assert.equal(
    querySessionPath("session/with spaces", "generate-sql"),
    "/api/nl2sql/query-sessions/session%2Fwith%20spaces/generate-sql"
  );
});

test("intent editor creates no patch until a business field is explicitly changed", () => {
  const intent: QuestionIntentGraph = {
    version: 4,
    question_original: "受注件数を表示",
    question_effective: "受注件数を表示",
    entities: [
      {
        id: "entity-order",
        ontology_node_id: "business-order",
        name_ja: "受注",
        role: "subject",
        physical_object_ids: ["physical-orders"],
      },
    ],
    metrics: [],
    dimensions: [],
    filters: [],
    granularity: "",
    sorts: [],
    limit: 100,
    candidate_paths: [],
    selected_path_id: null,
  };

  const draft = createIntentEditorDraft(intent);
  const result = buildIntentEditorPatch(intent, draft, "利用者がフォームで変更");

  assert.equal(result.patch.base_version, 4);
  assert.deepEqual(result.patch.operations, []);
  assert.deepEqual(result.errors, []);
});

test("intent editor builds a version-bound structured patch from approved graph choices", () => {
  const intent: QuestionIntentGraph = {
    version: 7,
    question_original: "売上を見たい",
    question_effective: "売上を見たい",
    entities: [
      {
        id: "entity-order",
        ontology_node_id: "business-order",
        name_ja: "受注",
        role: "subject",
        physical_object_ids: ["physical-orders"],
      },
    ],
    metrics: [],
    dimensions: [],
    filters: [],
    granularity: "",
    sorts: [],
    limit: 100,
    candidate_paths: [
      {
        id: "path-order-customer",
        name_ja: "受注から顧客",
        node_ids: ["business-order", "business-customer"],
        edge_ids: ["edge-order-customer"],
        approved: true,
      },
    ],
    selected_path_id: null,
  };
  const businessGraph: OntologyGraph = {
    nodes: [
      {
        id: "business-order",
        kind: "business_entity",
        business_name_ja: "受注",
        review_status: "approved",
        physical_mappings: [
          {
            object_ref: {
              node_id: "physical-orders",
              owner: "APP",
              object_name: "ORDERS",
              object_type: "table",
            },
          },
        ],
      },
      {
        id: "metric-sales",
        kind: "metric",
        business_name_ja: "売上金額",
        review_status: "approved",
      },
      {
        id: "property-order-date",
        kind: "property",
        business_name_ja: "受注日",
        review_status: "approved",
      },
      {
        id: "property-status",
        kind: "property",
        business_name_ja: "受注状態",
        review_status: "approved",
      },
    ],
    edges: [],
  };
  const metricOption = intentEditorNodeOptions(businessGraph, "metric")[0];
  const propertyOptions = intentEditorNodeOptions(businessGraph, "property");
  const dateOption = propertyOptions.find((option) => option.id === "property-order-date");
  const statusOption = propertyOptions.find((option) => option.id === "property-status");
  assert.ok(metricOption);
  assert.ok(dateOption);
  assert.ok(statusOption);

  const draft = createIntentEditorDraft(intent);
  draft.questionEffective = "確定した受注の月別売上金額";
  draft.metrics = [intentEditorConceptFromOption(metricOption, "metric", 1)];
  draft.metrics[0]!.aggregation = "SUM";
  draft.dimensions = [intentEditorConceptFromOption(dateOption, "dimension", 1)];
  draft.dimensions[0]!.granularity = "month";
  draft.filters = [intentEditorFilterFromOption(statusOption, 1)];
  draft.filters[0]!.valueText = "確定";
  draft.timeRange = {
    propertyNodeId: dateOption.id,
    labelJa: dateOption.label,
    start: "2026-06-01",
    end: "2026-06-30",
    startInclusive: true,
    endInclusive: true,
    relativeExpression: "先月",
    timezone: "Asia/Tokyo",
  };
  draft.granularity = "month";
  draft.sorts = [intentEditorSort(draft.metrics[0]!.id, 1)];
  draft.sorts[0]!.direction = "desc";
  draft.limit = "50";
  draft.selectedPathId = "path-order-customer";

  const result = buildIntentEditorPatch(intent, draft, "利用者がフォームで変更");
  const operations = new Map(result.patch.operations.map((operation) => [operation.path, operation]));

  assert.equal(result.patch.base_version, 7);
  assert.deepEqual(result.errors, []);
  assert.equal(operations.get("/question_effective")?.value, "確定した受注の月別売上金額");
  assert.deepEqual(operations.get("/metrics")?.value, [
    {
      id: draft.metrics[0]!.id,
      ontology_node_id: "metric-sales",
      name_ja: "売上金額",
      aggregation: "SUM",
      formula_description_ja: "",
    },
  ]);
  assert.deepEqual(operations.get("/filters")?.value, [
    {
      id: draft.filters[0]!.id,
      property_node_id: "property-status",
      label_ja: "受注状態",
      operator: "=",
      value: "確定",
      value_type: "string",
      required: false,
    },
  ]);
  assert.deepEqual(operations.get("/time_range")?.value, {
    property_node_id: "property-order-date",
    label_ja: "受注日",
    start: "2026-06-01",
    end: "2026-06-30",
    start_inclusive: true,
    end_inclusive: true,
    relative_expression: "先月",
    timezone: "Asia/Tokyo",
  });
  assert.equal(operations.get("/granularity")?.value, "month");
  assert.equal(operations.get("/limit")?.value, 50);
  assert.equal(operations.get("/selected_path_id")?.value, "path-order-customer");
});
