import assert from "node:assert/strict";
import test from "node:test";

import {
  layoutOntologyGraphSemanticMatrix,
  ontologyGraphSemanticLaneForKind,
} from "../src/features/nl2sql/ontology/graphLayout.ts";
import type { OntologyGraph } from "../src/features/nl2sql/ontology/types.ts";

test("semantic matrix レイアウトは種別を固定レーンへ配置する", () => {
  const graph: OntologyGraph = {
    nodes: [
      { id: "entity", kind: "business_entity", business_name_ja: "従業員" },
      { id: "metric", kind: "metric", business_name_ja: "人数" },
      {
        id: "table",
        kind: "table",
        business_name_ja: "従業員表",
        metadata: { owner: "ADMIN", object_name: "EMPLOYEE" },
      },
      {
        id: "column",
        kind: "column",
        business_name_ja: "従業員ID",
        technical_name: "ADMIN.EMPLOYEE.EMPLOYEE_ID",
        metadata: { owner: "ADMIN", object_name: "EMPLOYEE", column_name: "EMPLOYEE_ID" },
      },
    ],
    edges: [
      {
        id: "maps",
        source_node_id: "entity",
        target_node_id: "table",
        relationship_name_ja: "物理マッピング",
      },
    ],
  };

  const layout = layoutOntologyGraphSemanticMatrix(graph);
  const lanes = new Map(layout.lanes.map((lane) => [lane.id, lane]));

  assert.equal(layout.laneByNodeId.get("entity"), "business");
  assert.equal(layout.laneByNodeId.get("metric"), "attribute");
  assert.equal(layout.laneByNodeId.get("table"), "physical");
  assert.equal(layout.laneByNodeId.get("column"), "detail");
  assert.ok((lanes.get("business")?.y ?? 0) < (lanes.get("attribute")?.y ?? 0));
  assert.ok((lanes.get("attribute")?.y ?? 0) < (lanes.get("physical")?.y ?? 0));
  assert.ok((lanes.get("physical")?.y ?? 0) < (lanes.get("detail")?.y ?? 0));
  assert.equal(ontologyGraphSemanticLaneForKind("view"), "physical");
});

test("semantic matrix は同じ物理 object の業務概念・表・列を同一クラスタに揃える", () => {
  const graph: OntologyGraph = {
    nodes: [
      {
        id: "employee-business",
        kind: "business_entity",
        business_name_ja: "従業員",
        physical_mappings: [
          {
            object_ref: {
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
        metadata: { owner: "ADMIN", object_name: "EMPLOYEE", object_type: "TABLE" },
      },
      {
        id: "department-table",
        kind: "table",
        business_name_ja: "部署情報",
        metadata: { owner: "ADMIN", object_name: "DEPARTMENT", object_type: "TABLE" },
      },
      {
        id: "employee-id",
        kind: "column",
        business_name_ja: "従業員ID",
        technical_name: "ADMIN.EMPLOYEE.EMPLOYEE_ID",
        metadata: { owner: "ADMIN", object_name: "EMPLOYEE", column_name: "EMPLOYEE_ID", ordinal: 1 },
      },
      {
        id: "employee-name",
        kind: "column",
        business_name_ja: "従業員氏名",
        technical_name: "ADMIN.EMPLOYEE.EMPLOYEE_NAME",
        metadata: { owner: "ADMIN", object_name: "EMPLOYEE", column_name: "EMPLOYEE_NAME", ordinal: 2 },
      },
    ],
    edges: [],
  };

  const first = layoutOntologyGraphSemanticMatrix(graph);
  const second = layoutOntologyGraphSemanticMatrix(graph);
  assert.deepEqual([...first.positions.entries()], [...second.positions.entries()]);
  assert.equal(
    first.clusterByNodeId.get("employee-business"),
    first.clusterByNodeId.get("employee-table")
  );
  assert.equal(first.clusterByNodeId.get("employee-id"), first.clusterByNodeId.get("employee-table"));
  assert.equal(first.clusterByNodeId.get("employee-name"), first.clusterByNodeId.get("employee-table"));
  assert.notEqual(
    first.clusterByNodeId.get("department-table"),
    first.clusterByNodeId.get("employee-table")
  );
  assert.equal(first.positions.get("employee-id")?.x, first.positions.get("employee-table")?.x);
  assert.equal(first.positions.get("employee-name")?.x, first.positions.get("employee-table")?.x);
  assert.ok((first.positions.get("employee-name")?.y ?? 0) > (first.positions.get("employee-id")?.y ?? 0));
});

test("semantic matrix はノードのないレーンを生成しない(空レーンラベル抑止)", () => {
  const graph: OntologyGraph = {
    nodes: [
      {
        id: "only-table",
        kind: "table",
        business_name_ja: "従業員情報",
        technical_name: "ADMIN.EMPLOYEE",
      },
    ],
    edges: [],
  };
  const layout = layoutOntologyGraphSemanticMatrix(graph);
  assert.deepEqual(
    layout.lanes.map((lane) => lane.id),
    ["physical"]
  );
  assert.ok(layout.lanes.every((lane) => lane.nodeCount > 0));
});
