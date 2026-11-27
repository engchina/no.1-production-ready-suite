import { describe, expect, it } from "vitest";
import { directedGraphBundles, expandedGraphNeighborhood, graphNeighborGroups, reachableGraphBranches } from "../src/lib/graph-exploration";

describe("関係の探索", () => {
  it("逆方向の矢印と同方向の三つの内訳を保持する", () => {
    const relations = ["AB", "AB", "BA", "AB"];
    expect([...directedGraphBundles(relations, (edge) => [edge[0], edge[1]]).values()]).toEqual([["AB", "AB", "AB"], ["BA"]]);
  });

  it("256 個の節点を分割し、別の展開元・選択・検索一致を保ったまま閉じる", () => {
    const ids = ["a", "b", ...Array.from({ length: 254 }, (_, i) => String(i))];
    const edges = ["a", "b"].flatMap((source) => ids.slice(2).map((target) => ({ source, target, kind: "reference" })));
    const groups = graphNeighborGroups(edges, 12);
    const limits = new Map([[groups[0].id, 24], [groups[1].id, 12]]);
    const pinned = new Set(["200", "201"]);
    expect(expandedGraphNeighborhood(ids, groups, limits, pinned).nodes.size).toBe(28);
    limits.delete(groups[0].id);
    const { nodes: visible, showsRelation } = expandedGraphNeighborhood(ids, groups, limits, pinned);
    expect(visible.size).toBe(16);
    expect(showsRelation("a", "11", "reference")).toBe(false);
    expect(showsRelation("b", "11", "reference")).toBe(true);
    expect(visible.has("11")).toBe(true);
    expect(visible.has("12")).toBe(false);
    expect(visible.has("200")).toBe(true);
  });

  it("枝を閉じた後も別の親から辿れる孫と循環を一度だけ表示する", () => {
    const branches = [{ owner: "A", ids: ["C", "D"] }, { owner: "B", ids: ["C"] }, { owner: "C", ids: ["E", "B"] }, { owner: "D", ids: ["F"] }];
    const reachable = (input: typeof branches) => reachableGraphBranches(["A", "B"], input, (b) => b.owner, (b) => b.ids);
    expect(reachable(branches)).toHaveLength(4);
    expect(reachable(branches.slice(1)).map((b) => b.owner)).toEqual(["B", "C"]);
  });
});
