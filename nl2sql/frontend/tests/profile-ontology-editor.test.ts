import assert from "node:assert/strict";
import test from "node:test";

import {
  createProfileOntologyDraftPayload,
  displayProfileOntologyGraph,
  profileOntologyObjectFromNode,
  type ProfileOntologyDraftState,
} from "../src/features/nl2sql/ontology/ProfileOntologyEditorCore.ts";
import type { OntologyGraph } from "../src/features/nl2sql/ontology/types.ts";

// backend GET /nl2sql/profiles/{id}/ontology-view が返す profile スコープ済みグラフの縮約 fixture。
const backendGraph: OntologyGraph = {
  revision: {
    id: "ontology_revision:fp7:1",
    version: 3,
    status: "published",
    schema_fingerprint: "schema-fingerprint-7",
    etag: "etag-7",
  },
  nodes: [
    {
      id: "table:APP:ORDERS",
      kind: "table",
      business_name_ja: "受注",
      physical_mappings: [
        {
          object_ref: {
            node_id: "table:APP:ORDERS",
            owner: "APP",
            object_name: "ORDERS",
            object_type: "table",
          },
        },
      ],
      review_status: "approved",
    },
    {
      id: "table:APP:CUSTOMERS",
      kind: "table",
      business_name_ja: "顧客",
      physical_mappings: [
        {
          object_ref: {
            node_id: "table:APP:CUSTOMERS",
            owner: "APP",
            object_name: "CUSTOMERS",
            object_type: "table",
          },
        },
      ],
      review_status: "approved",
    },
    {
      id: "view:SALES:V_ORDER_TOTALS",
      kind: "view",
      business_name_ja: "受注集計",
      physical_mappings: [
        {
          object_ref: {
            node_id: "view:SALES:V_ORDER_TOTALS",
            owner: "SALES",
            object_name: "V_ORDER_TOTALS",
            object_type: "view",
          },
        },
      ],
      review_status: "approved",
    },
    {
      id: "column:APP:ORDERS:ORDER_ID",
      kind: "column",
      business_name_ja: "受注 ID",
      review_status: "approved",
    },
    {
      id: "schema:APP:APP",
      kind: "schema",
      business_name_ja: "APP",
      review_status: "approved",
    },
  ],
  edges: [
    {
      id: "fk:APP:ORDERS:FK_ORDERS_CUSTOMER",
      kind: "foreign_key",
      source_node_id: "table:APP:ORDERS",
      target_node_id: "table:APP:CUSTOMERS",
      relationship_name_ja: "顧客 を参照",
      cardinality: "many_to_one",
      review_status: "approved",
    },
    {
      id: "lineage:SALES:V_ORDER_TOTALS:APP:ORDERS",
      kind: "lineage",
      source_node_id: "view:SALES:V_ORDER_TOTALS",
      target_node_id: "table:APP:ORDERS",
      relationship_name_ja: "参照データ",
      cardinality: "unknown",
      review_status: "proposed",
    },
    {
      id: "contains:APP:ORDERS:ORDER_ID",
      kind: "contains",
      source_node_id: "table:APP:ORDERS",
      target_node_id: "column:APP:ORDERS:ORDER_ID",
      relationship_name_ja: "含む",
      review_status: "approved",
    },
  ],
};

const emptyDraft: ProfileOntologyDraftState = { nodes: {}, edges: {} };

test("表示グラフは列・schema ノードを省き、対象間の関係だけを残す", () => {
  const graph = displayProfileOntologyGraph(backendGraph, emptyDraft);

  assert.deepEqual(
    graph.nodes.map((node) => node.id).sort(),
    ["table:APP:CUSTOMERS", "table:APP:ORDERS", "view:SALES:V_ORDER_TOTALS"]
  );
  assert.deepEqual(
    graph.edges.map((edge) => edge.id).sort(),
    ["fk:APP:ORDERS:FK_ORDERS_CUSTOMER", "lineage:SALES:V_ORDER_TOTALS:APP:ORDERS"]
  );
  assert.equal(graph.revision_id, "ontology_revision:fp7:1");
  assert.equal(graph.revision?.schema_fingerprint, "schema-fingerprint-7");
});

test("draft override は表示名・基数・許可フラグへ反映される", () => {
  const draft: ProfileOntologyDraftState = {
    nodes: {
      "table:APP:ORDERS": { business_name_ja: "確定受注", table_usage: "売上集計の起点" },
    },
    edges: {
      "fk:APP:ORDERS:FK_ORDERS_CUSTOMER": { cardinality: "one_to_one", allowed_path: true },
    },
  };
  const graph = displayProfileOntologyGraph(backendGraph, draft);

  assert.equal(
    graph.nodes.find((node) => node.id === "table:APP:ORDERS")?.business_name_ja,
    "確定受注"
  );
  const fk = graph.edges.find((edge) => edge.id === "fk:APP:ORDERS:FK_ORDERS_CUSTOMER");
  assert.equal(fk?.cardinality, "one_to_one");
  assert.equal(fk?.enabled, true);
});

test("グラフ未取得(null)のときは空グラフとして扱う", () => {
  const graph = displayProfileOntologyGraph(null, emptyDraft);
  assert.deepEqual(graph, { nodes: [], edges: [] });
});

test("draft payload は backend node/edge id に基づき、承認済み関係だけを許可 path に含める", () => {
  const draft: ProfileOntologyDraftState = {
    nodes: {
      "table:APP:ORDERS": { business_name_ja: "確定受注", table_usage: "売上集計の起点" },
      "business:APP:TABLE:LEGACY": { table_usage: "旧ローカル id は無視される" },
    },
    edges: {
      "fk:APP:ORDERS:FK_ORDERS_CUSTOMER": { cardinality: "many_to_one", allowed_path: true },
      "lineage:SALES:V_ORDER_TOTALS:APP:ORDERS": { allowed_path: true },
      "fk:UNKNOWN:EDGE": { allowed_path: true },
    },
  };
  const payload = createProfileOntologyDraftPayload(
    "finance-profile",
    backendGraph,
    ["ORDERS", "APP.CUSTOMERS", "OUTSIDE_TABLE"],
    ["SALES.V_ORDER_TOTALS"],
    draft
  );

  assert.equal(payload.profile_id, "finance-profile");
  assert.equal(payload.schema_fingerprint, "schema-fingerprint-7");
  assert.deepEqual(payload.node_overrides, [
    {
      node_id: "table:APP:ORDERS",
      business_name_ja: "確定受注",
      table_usage: "売上集計の起点",
    },
  ]);
  assert.deepEqual(payload.table_usage, {
    "table:APP:ORDERS": "売上集計の起点",
  });
  // 承認済み(approved)の FK だけが許可 path になる。lineage は proposed のため除外。
  assert.deepEqual(payload.allowed_path_ids, ["fk:APP:ORDERS:FK_ORDERS_CUSTOMER"]);
  assert.deepEqual(
    payload.edge_overrides.map((override) => override.edge_id),
    ["fk:APP:ORDERS:FK_ORDERS_CUSTOMER", "lineage:SALES:V_ORDER_TOTALS:APP:ORDERS"]
  );
  // view 範囲外の物理 object は送らない。
  assert.deepEqual(payload.physical_scope, {
    table_names: ["ORDERS", "APP.CUSTOMERS"],
    view_names: ["SALES.V_ORDER_TOTALS"],
  });
});

test("physical_mappings と legacy physical_mapping の両形式からオブジェクト参照を解決できる", () => {
  const backendNode = backendGraph.nodes.find((node) => node.id === "table:APP:ORDERS");
  assert.deepEqual(profileOntologyObjectFromNode(backendNode), {
    owner: "APP",
    objectName: "ORDERS",
    objectType: "table",
    qualifiedName: "APP.ORDERS",
  });

  assert.deepEqual(
    profileOntologyObjectFromNode({
      id: "legacy",
      kind: "view",
      business_name_ja: "旧形式",
      physical_mapping: { owner: "sales", object_name: "v_order_totals", object_type: "VIEW" },
    }),
    {
      owner: "SALES",
      objectName: "V_ORDER_TOTALS",
      objectType: "view",
      qualifiedName: "SALES.V_ORDER_TOTALS",
    }
  );

  assert.equal(
    profileOntologyObjectFromNode({ id: "none", kind: "metric", business_name_ja: "指標" }),
    null
  );
});
