import { aggregateGraphEdges, GRAPH_NODE_CARD_SIZE } from "@production-ready/ui/graph";
import { describe, expect, it } from "vitest";

import type { StructureGraphOverview, StructureGraphTreeNode } from "@/lib/api";

import { emptyStructureTree, layoutStructure, moreNodeId, structureReferenceChains, stripExtension, visibleStructureNodes, type StructureTreeState } from "./structure-graph";

const documentNode = (id: string, extra = {}) => ({ id, document_id: `d-${id}`, chunk_set_id: `s-${id}`, name: `${id}.pdf`, has_sections: true, outgoing_count: 0, incoming_count: 0, ...extra });
const overview: StructureGraphOverview = {
  status: "ok",
  documents: [documentNode("a"), documentNode("b"), documentNode("c")],
  edges: [{ id: "a:b", source: "a", target: "b", count: 5 }],
};
const tree = (id: string, parent_id: string | null, child_count = 0, type = "section"): StructureGraphTreeNode => ({
  id, name: id, label: id, type, document_id: "d-a", document_name: "a.pdf", chunk_set_id: "s-a", parent_id, virtual: false, child_count,
});
const opened: StructureTreeState = {
  ...emptyStructureTree(),
  nodes: new Map([["a1", tree("a1", null, 1)], ["a11", tree("a11", "a1")], ["a2", tree("a2", null)]]),
  children: new Map([["a", { ids: ["a1", "a2"], total: 5 }], ["a1", { ids: ["a11"], total: 1 }]]),
  expanded: new Set(["a"]),
};
const all = () => true;

describe("visibleStructureNodes", () => {
  it("閉じていると全文書だけを出す", () => {
    expect(visibleStructureNodes(overview, emptyStructureTree(), all).map((node) => node.id)).toEqual(["a", "b", "c"]);
  });

  it("開いた段と、続きの節点を出す", () => {
    const nodes = visibleStructureNodes(overview, opened, all);
    expect(nodes.map((node) => node.id)).toEqual(["a", "a1", "a2", moreNodeId("a"), "b", "c"]);
    expect(nodes.find((node) => node.kind === "more")?.remaining).toBe(3);
    expect(nodes.find((node) => node.id === "a1")?.expandable).toBe(true);
    const deeper = visibleStructureNodes(overview, { ...opened, expanded: new Set(["a", "a1"]) }, all);
    expect(deeper.map((node) => node.id)).toContain("a11");
  });

  it("種類で隠しても、下の段と続きの節点は残す", () => {
    const nodes = visibleStructureNodes(overview, opened, (kind) => kind !== "document");
    expect(nodes.map((node) => node.id)).toEqual(["a1", "a2", moreNodeId("a")]);
  });
});

describe("structureReferenceChains", () => {
  it("閉じた文書の組は総覧の件数で出す", () => {
    expect(structureReferenceChains(overview, emptyStructureTree())).toEqual([{ source: ["a"], target: ["b"], count: 5 }]);
  });

  it("開いた文書は章節の列にし、両端で読んだ同じ参照を 1 回に数える", () => {
    const item = { source_set: "s-a", target_set: "s-b", source_chain: ["a1"], target_chain: ["b1"], count: 5 };
    const state = { ...emptyStructureTree(), references: new Map([["s-a", [item]], ["s-b", [item]]]) };
    const chains = structureReferenceChains(overview, state);
    expect(chains).toEqual([{ source: ["a", "a1"], target: ["b", "b1"], count: 5 }]);
    expect(aggregateGraphEdges(chains, new Set(["a", "b"]))).toEqual([{ id: "a:b", source: "a", target: "b", count: 5 }]);
  });
});

describe("layoutStructure", () => {
  it("参照のある文書は向きの順に、参照の無い文書はその右に置く", () => {
    const nodes = visibleStructureNodes(overview, emptyStructureTree(), all);
    const positions = layoutStructure(nodes, [{ source: "a", target: "b" }], new Map());
    expect(positions.get("a")!.x).toBeLessThan(positions.get("b")!.x);
    expect(positions.get("c")!.x).toBeGreaterThan(positions.get("b")!.x + GRAPH_NODE_CARD_SIZE.width);
  });

  it("開いた子は親の右に置き、他の文書と重ねない", () => {
    const nodes = visibleStructureNodes(overview, opened, all);
    const positions = layoutStructure(nodes, [{ source: "a", target: "b" }], new Map());
    expect(positions.get("a1")!.x).toBeGreaterThan(positions.get("a")!.x);
    expect(positions.get("b")!.x).toBeGreaterThanOrEqual(positions.get("a1")!.x + GRAPH_NODE_CARD_SIZE.width);
    const seen = new Set([...positions.values()].map((point) => `${point.x}:${point.y}`));
    expect(seen.size).toBe(positions.size);
  });

  it("循環でも有限の位置を返す", () => {
    const nodes = visibleStructureNodes(overview, emptyStructureTree(), all);
    const positions = layoutStructure(nodes, [{ source: "a", target: "b" }, { source: "b", target: "a" }], new Map());
    expect([...positions.values()].every((point) => Number.isFinite(point.x) && Number.isFinite(point.y))).toBe(true);
  });
});

it("stripExtension は拡張子だけを外し、番号の接頭辞を残す", () => {
  expect(stripExtension("A001_合成.json")).toBe("A001_合成");
  expect(stripExtension("拡張子なし")).toBe("拡張子なし");
});
