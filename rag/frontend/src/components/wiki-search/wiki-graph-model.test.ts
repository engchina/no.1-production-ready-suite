import { describe, expect, it } from "vitest";
import {
  addToWikiGraph,
  collapseWikiNode,
  drawnWikiGraph,
  edgeKey,
  emptyWikiGraph,
  keepWikiNodes,
  moreId,
  recordNeighbors,
  remainingNeighbors,
  removeWikiNodes,
  replaceWikiGraph,
  showNeighbors,
  unitsToConnect,
  wikiGraphRows,
  type WikiGraphNode,
  type WikiNeighbor,
} from "./wiki-graph-model";

const node = (id: string, kind: WikiGraphNode["kind"] = "unit"): WikiGraphNode => ({ id, kind, label: id, document_id: "doc", document_name: "合成資料.md" });
const neighbor = (owner: string, id: string): WikiNeighbor => ({ node: node(id), edge: { id: edgeKey(owner, "reference", id), source: owner, target: id, kind: "reference" } });

describe("wiki graph model", () => {
  it("shows neighbors in batches and keeps the rest behind a more node", () => {
    let state = replaceWikiGraph(emptyWikiGraph(), [node("root")], []);
    state = recordNeighbors(state, "root", Array.from({ length: 30 }, (_, i) => neighbor("root", "n" + i)), true);
    state = showNeighbors(state, "root", 25);
    expect(state.nodes.size).toBe(26);
    expect(remainingNeighbors(state, "root")).toBe(5);
    const drawn = drawnWikiGraph(state);
    expect(drawn.nodes.find((item) => item.id === moreId("root"))?.remaining).toBe(5);
    state = showNeighbors(state, "root", 25);
    expect(remainingNeighbors(state, "root")).toBe(0);
    expect(drawnWikiGraph(state).nodes.some((item) => item.kind === "more")).toBe(false);
  });

  it("adds edges to neighbors already on the canvas without counting them", () => {
    let state = replaceWikiGraph(emptyWikiGraph(), [node("root"), node("n0")], []);
    state = recordNeighbors(state, "root", [neighbor("root", "n0"), neighbor("root", "n1")], true);
    state = showNeighbors(state, "root", 1);
    expect([...state.nodes.keys()].sort()).toEqual(["n0", "n1", "root"]);
    expect(drawnWikiGraph(state).edges).toHaveLength(2);
  });

  it("collapses what an expansion added, keeps search matches, and removes or keeps selected nodes", () => {
    let state = replaceWikiGraph(emptyWikiGraph(), [node("root")], []);
    state = recordNeighbors(state, "root", [neighbor("root", "a"), neighbor("root", "b")], true);
    state = showNeighbors(state, "root", 25);
    state = recordNeighbors(state, "a", [neighbor("a", "c")], true);
    state = showNeighbors(state, "a", 25);
    state = addToWikiGraph(state, [], [], { matched: ["b"] });
    const collapsed = collapseWikiNode(state, "root");
    expect([...collapsed.nodes.keys()].sort()).toEqual(["b", "root"]);
    expect([...removeWikiNodes(state, ["c"]).nodes.keys()]).not.toContain("c");
    expect([...keepWikiNodes(state, ["a", "b"]).nodes.keys()].sort()).toEqual(["a", "b"]);
  });

  it("replacing the graph bumps the generation and clears expansions", () => {
    let state = replaceWikiGraph(emptyWikiGraph(), [node("root")], []);
    state = recordNeighbors(state, "root", [neighbor("root", "a")], false, undefined, 40);
    expect(remainingNeighbors(state, "root")).toBe(40);
    const next = replaceWikiGraph(state, [node("x")], [], ["x"]);
    expect(next.generation).toBe(state.generation + 1);
    expect(next.expansions.size).toBe(0);
    expect(next.matched.has("x")).toBe(true);
  });

  it("lists the nodes on the canvas by name with the number of drawn relations", () => {
    let state = replaceWikiGraph(emptyWikiGraph(), [node("b"), node("a"), node("doc", "document")], [
      { id: edgeKey("doc", "contains", "a"), source: "doc", target: "a", kind: "contains" },
      { id: edgeKey("a", "reference", "b"), source: "a", target: "b", kind: "reference" },
      // 相手が図に無い線は数えない。
      { id: edgeKey("a", "reference", "gone"), source: "a", target: "gone", kind: "reference" },
    ]);
    expect(wikiGraphRows(state).map((row) => [row.node.id, row.relations])).toEqual([["a", 2], ["b", 1], ["doc", 1]]);
    // 外したノードは一覧からも消え、相手の関係の数も減る（図と同じ集合）。
    state = removeWikiNodes(state, ["b"]);
    expect(wikiGraphRows(state).map((row) => [row.node.id, row.relations])).toEqual([["a", 1], ["doc", 1]]);
    // 「さらに展開」の点は一覧に出さない。
    state = recordNeighbors(state, "a", [neighbor("a", "n1")], true);
    expect(drawnWikiGraph(state).nodes.some((item) => item.kind === "more")).toBe(true);
    expect(wikiGraphRows(state).some((row) => row.node.kind === "more")).toBe(false);
  });

  it("asks for connections once per newly added unit, new units first, within the API limit", () => {
    let state = replaceWikiGraph(emptyWikiGraph(), [node("doc", "document"), node("u1")], []);
    // 原文が 1 件だけなら照会しない。
    expect(unitsToConnect(state, 200)).toEqual([]);
    // これから足す原文（図内検索の当たり）と、図にある原文を合わせて照会する。
    expect(unitsToConnect(state, 200, ["u2", "u3"])).toEqual(["u2", "u3", "u1"]);
    state = addToWikiGraph(state, [node("u2"), node("u3")], [], { linked: ["u1", "u2", "u3"] });
    // 照会済みの原文だけなら照会しない。
    expect(unitsToConnect(state, 200)).toEqual([]);
    // 展開で足した原文を先頭に、上限まで。文書・章節は含めない。
    state = addToWikiGraph(state, [node("u4"), node("sec", "section")], []);
    expect(unitsToConnect(state, 200)).toEqual(["u4", "u1", "u2", "u3"]);
    expect(unitsToConnect(state, 2)).toEqual(["u4", "u1"]);
    // 外した原文は照会の記録からも消える（足し直したらもう一度照会する）。図を置き換えると記録は空になる。
    state = removeWikiNodes(addToWikiGraph(state, [], [], { linked: ["u4"] }), ["u4"]);
    expect(state.linked.has("u4")).toBe(false);
    expect(replaceWikiGraph(state, [node("x")], []).linked.size).toBe(0);
  });
});
