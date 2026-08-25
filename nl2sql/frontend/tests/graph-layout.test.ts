import assert from "node:assert/strict";
import test from "node:test";

import {
  fitLayoutToBounds,
  layoutOntologyGraph,
  layoutOntologyGraphSemanticMatrix,
  ontologyGraphSemanticLaneForKind,
} from "../src/features/nl2sql/ontology/graphLayout.ts";
import type { OntologyGraph } from "../src/features/nl2sql/ontology/types.ts";

function sampleGraph(nodeCount: number): OntologyGraph {
  const nodes = Array.from({ length: nodeCount }, (_, index) => ({
    id: `node-${index}`,
    kind: "table" as const,
    business_name_ja: `表_${index}`,
  }));
  const edges = Array.from({ length: Math.max(nodeCount - 1, 0) }, (_, index) => ({
    id: `edge-${index}`,
    source_node_id: `node-${index}`,
    target_node_id: `node-${index + 1}`,
    relationship_name_ja: "参照",
  }));
  return { nodes, edges };
}

test("force レイアウトは決定論(同じグラフ → 同じ座標)", () => {
  const graph = sampleGraph(12);
  const first = layoutOntologyGraph(graph);
  const second = layoutOntologyGraph(graph);
  assert.deepEqual([...first.entries()], [...second.entries()]);
  assert.equal(first.size, 12);
});

test("隣接しないノード同士も重ならない距離を保つ", () => {
  const graph = sampleGraph(16);
  const positions = [...layoutOntologyGraph(graph).values()];
  for (let i = 0; i < positions.length; i += 1) {
    for (let j = i + 1; j < positions.length; j += 1) {
      const distance = Math.hypot(
        positions[i].x - positions[j].x,
        positions[i].y - positions[j].y
      );
      // 衝突半径 118 の 2 ノードはほぼ 2r 離れる。緩めの下限で崩壊だけを検知する。
      assert.ok(distance > 120, `ノード ${i}-${j} が近すぎます: ${distance}`);
    }
  }
});

test("グラフ外のノードを参照する関係は無視して座標を返す", () => {
  const graph = sampleGraph(3);
  graph.edges.push({
    id: "edge-dangling",
    source_node_id: "node-0",
    target_node_id: "node-unknown",
    relationship_name_ja: "参照",
  });
  const positions = layoutOntologyGraph(graph);
  assert.equal(positions.size, 3);
});

test("空グラフは空 Map", () => {
  assert.equal(layoutOntologyGraph({ nodes: [], edges: [] }).size, 0);
});

test("fitLayoutToBounds は領域内へ収め、単一ノードは中央へ置く", () => {
  const fitted = fitLayoutToBounds(layoutOntologyGraph(sampleGraph(10)), 1000, 600, 50);
  fitted.forEach((point) => {
    assert.ok(point.x >= 0 && point.x <= 1000, `x=${point.x}`);
    assert.ok(point.y >= 0 && point.y <= 600, `y=${point.y}`);
  });

  const single = fitLayoutToBounds(
    new Map([["only", { x: 42, y: -7 }]]),
    1000,
    600,
    50
  );
  const point = single.get("only");
  assert.ok(Math.abs((point?.x ?? 0) - 500) < 1);
  assert.ok(Math.abs((point?.y ?? 0) - 300) < 1);
});

test("layered レイアウトは決定論で、ノードが重ならない", async () => {
  const { layoutOntologyGraphLayered } = await import(
    "../src/features/nl2sql/ontology/graphLayout.ts"
  );
  const graph = sampleGraph(8);
  const first = layoutOntologyGraphLayered(graph);
  const second = layoutOntologyGraphLayered(graph);
  assert.deepEqual([...first.entries()], [...second.entries()]);
  assert.equal(first.size, 8);
  // LR の layered 配置: 直列チェーンなので x が単調に進む
  const xs = graph.nodes.map((node) => first.get(node.id)?.x ?? 0);
  for (let index = 1; index < xs.length; index += 1) {
    assert.ok(xs[index] > xs[index - 1], `x should advance: ${xs[index - 1]} -> ${xs[index]}`);
  }
  // ノード矩形(200x64)が重ならない
  const points = [...first.values()];
  for (let a = 0; a < points.length; a += 1) {
    for (let b = a + 1; b < points.length; b += 1) {
      const overlap =
        Math.abs(points[a].x - points[b].x) < 200 && Math.abs(points[a].y - points[b].y) < 64;
      assert.equal(overlap, false, "nodes must not overlap");
    }
  }
});

test("layered レイアウトは空グラフで空 Map を返す", async () => {
  const { layoutOntologyGraphLayered } = await import(
    "../src/features/nl2sql/ontology/graphLayout.ts"
  );
  assert.equal(layoutOntologyGraphLayered({ nodes: [], edges: [] }).size, 0);
});

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
