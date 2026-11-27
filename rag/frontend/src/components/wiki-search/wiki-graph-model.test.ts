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
});
