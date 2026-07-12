import assert from "node:assert/strict";
import test from "node:test";

import {
  fitLayoutToBounds,
  layoutOntologyGraph,
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
