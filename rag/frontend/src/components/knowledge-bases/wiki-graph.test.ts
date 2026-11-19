import { GRAPH_NODE_CARD_SIZE } from "@production-ready/ui/graph";
import { describe, expect, it } from "vitest";

import type { WikiGraphOverview, WikiGraphTreeNode } from "@/lib/api";

import { emptyWikiTree, layoutWiki, moreNodeId, stripExtension, visibleWikiNodes, wikiRelationEdges, type WikiTreeState } from "./wiki-graph";

const documentNode = (id: string, extra = {}) => ({ id, document_id: id, name: `${id}.pdf`, unit_count: 3, outgoing_links: 0, incoming_links: 0, ...extra });
const overview: WikiGraphOverview = { status: "ok", documents: [documentNode("a"), documentNode("b"), documentNode("c", { unit_count: 0 })] };
const tree = (id: string, parent_id: string | null, extra: Partial<WikiGraphTreeNode> = {}): WikiGraphTreeNode => ({
  id, kind: "section", label: id, path: [id], document_id: "a", document_name: "a.pdf", parent_id, child_count: 0, unit_count: 0,
  outgoing_links: 0, incoming_links: 0, outgoing: [], summary: "", ...extra,
});
const opened: WikiTreeState = {
  ...emptyWikiTree(),
  nodes: new Map([
    ["s1", tree("s1", null, { child_count: 2 })],
    ["u1", tree("u1", "s1", { kind: "unit", summary: "合成の要約 1", outgoing: [{ target: "u2", relation: "exception", scope: "same_document" }, { target: "b1", relation: "reference", scope: "cross_document" }] })],
    ["u2", tree("u2", "s1", { kind: "unit", summary: "合成の要約 2", outgoing: [{ target: "u1", relation: "prerequisite", scope: "same_document" }] })],
    ["b1", tree("b1", null, { kind: "unit", document_id: "b", summary: "合成の要約 b" })],
  ]),
  children: new Map([["a", { ids: ["s1"], total: 3 }], ["s1", { ids: ["u1", "u2"], total: 2 }], ["b", { ids: ["b1"], total: 1 }]]),
  expanded: new Set(["a"]),
};
const all = () => true;

describe("visibleWikiNodes", () => {
  it("閉じていると文書だけを出し、単位の無い文書は開けない", () => {
    const nodes = visibleWikiNodes(overview, emptyWikiTree(), all);
    expect(nodes.map((node) => node.id)).toEqual(["a", "b", "c"]);
    expect(nodes.map((node) => node.expandable)).toEqual([true, true, false]);
  });

  it("開いた段と続きの節点を出し、章節を開くと単位まで出す", () => {
    const nodes = visibleWikiNodes(overview, opened, all);
    expect(nodes.map((node) => node.id)).toEqual(["a", "s1", moreNodeId("a"), "b", "c"]);
    expect(nodes.find((node) => node.kind === "more")?.remaining).toBe(2);
    const deeper = visibleWikiNodes(overview, { ...opened, expanded: new Set(["a", "s1"]) }, all);
    const unit = deeper.find((node) => node.id === "u1")!;
    // 同じ章節の単位は見出しが同じなので、案内の要約で見分ける。
    expect(unit.label).toBe("合成の要約 1");
    expect(unit.name).toBe("u1：合成の要約 1");
  });

  it("種類で隠しても、下の段は残す", () => {
    const nodes = visibleWikiNodes(overview, { ...opened, expanded: new Set(["a", "s1"]) }, (kind) => kind !== "section");
    expect(nodes.map((node) => node.id)).toEqual(["a", "u1", "u2", moreNodeId("a"), "b", "c"]);
  });
});

describe("wikiRelationEdges", () => {
  it("両端が表示中の単位の間だけに線を引き、向きを保つ", () => {
    const nodes = visibleWikiNodes(overview, { ...opened, expanded: new Set(["a", "s1"]) }, all);
    expect(wikiRelationEdges(nodes, all)).toEqual([
      { id: "same_document:u1:u2", source: "u1", target: "u2", relation: "exception", scope: "same_document" },
      { id: "same_document:u2:u1", source: "u2", target: "u1", relation: "prerequisite", scope: "same_document" },
    ]);
    // 別の文書の単位を開くと、文書をまたぐ関連の線も引く。
    const both = visibleWikiNodes(overview, { ...opened, expanded: new Set(["a", "s1", "b"]) }, all);
    expect(wikiRelationEdges(both, all).map((edge) => edge.id)).toContain("cross_document:u1:b1");
  });

  it("隠した関係の種類の線は引かない", () => {
    const nodes = visibleWikiNodes(overview, { ...opened, expanded: new Set(["a", "s1"]) }, all);
    expect(wikiRelationEdges(nodes, (relation) => relation !== "exception").map((edge) => edge.relation)).toEqual(["prerequisite"]);
  });
});

describe("layoutWiki", () => {
  it("関連でつながる文書は向きの順に、つながらない文書はその右に置く", () => {
    const nodes = visibleWikiNodes(overview, emptyWikiTree(), all);
    const positions = layoutWiki(nodes, [{ source: "a", target: "b" }], new Map());
    expect(positions.get("a")!.x).toBeLessThan(positions.get("b")!.x);
    expect(positions.get("c")!.x).toBeGreaterThan(positions.get("b")!.x + GRAPH_NODE_CARD_SIZE.width);
  });

  it("開いた子は親の右に置き、位置を重ねない", () => {
    const nodes = visibleWikiNodes(overview, { ...opened, expanded: new Set(["a", "s1"]) }, all);
    const positions = layoutWiki(nodes, [], new Map());
    expect(positions.get("s1")!.x).toBeGreaterThan(positions.get("a")!.x);
    expect(positions.get("u1")!.x).toBeGreaterThan(positions.get("s1")!.x);
    const seen = new Set([...positions.values()].map((point) => `${point.x}:${point.y}`));
    expect(seen.size).toBe(positions.size);
  });

  it("循環でも有限の位置を返す", () => {
    const nodes = visibleWikiNodes(overview, emptyWikiTree(), all);
    const positions = layoutWiki(nodes, [{ source: "a", target: "b" }, { source: "b", target: "a" }], new Map());
    expect([...positions.values()].every((point) => Number.isFinite(point.x) && Number.isFinite(point.y))).toBe(true);
  });
});

it("stripExtension は拡張子だけを外し、番号の接頭辞を残す", () => {
  expect(stripExtension("A001_合成.json")).toBe("A001_合成");
  expect(stripExtension("拡張子なし")).toBe("拡張子なし");
});
