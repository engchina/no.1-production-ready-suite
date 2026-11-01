import { describe, expect, it } from "vitest";

import { layoutGraphHierarchy, type GraphLayoutInput } from "../src/lib/graph-layout";

const input = (ids: string[], edges: GraphLayoutInput["edges"]): GraphLayoutInput => ({
  nodes: ids.map((id) => ({ id, width: 10, height: 4 })),
  edges,
  columnGap: 3,
  rowGap: 2,
});

describe("layoutGraphHierarchy", () => {
  it("places each child after its deepest parent, independent of edge order", () => {
    const edges = [{ source: "a", target: "c" }, { source: "b", target: "c" }, { source: "a", target: "b" }];
    const first = layoutGraphHierarchy(input(["a", "b", "c"], edges));
    expect(first.positions.get("a")?.x).toBe(0);
    expect(first.positions.get("b")?.x).toBe(13);
    expect(first.positions.get("c")?.x).toBe(26);
    expect(layoutGraphHierarchy(input(["a", "b", "c"], edges.slice().reverse()))).toEqual(first);
  });

  it("keeps disconnected roots and siblings in stable input order without overlap", () => {
    const result = layoutGraphHierarchy(input(["a", "b", "c", "d"], [{ source: "a", target: "c" }, { source: "a", target: "d" }]));
    expect(result.positions.get("a")?.y).toBeLessThan(result.positions.get("b")!.y);
    expect(result.positions.get("c")?.y).toBeLessThan(result.positions.get("d")!.y);
    expect(result.positions.get("d")!.y - result.positions.get("c")!.y).toBeGreaterThanOrEqual(6);
  });

  it("returns finite positions for cycles, self loops, duplicate and unloaded edges", () => {
    const result = layoutGraphHierarchy(input(["a", "b", "c"], [
      { source: "a", target: "b" }, { source: "b", target: "a" },
      { source: "b", target: "c" }, { source: "a", target: "a" },
      { source: "a", target: "b" }, { source: "missing", target: "c" },
    ]));
    expect(result.positions.size).toBe(3);
    expect(result.brokenCycleEdges).toEqual([{ source: "b", target: "a" }]);
    for (const point of result.positions.values()) {
      expect(Number.isFinite(point.x) && Number.isFinite(point.y)).toBe(true);
    }
  });

  it("uses measured dimensions and supports an empty graph", () => {
    const request = input(["a", "b"], [{ source: "a", target: "b" }]);
    request.nodes[0] = { id: "a", width: 30, height: 12 };
    expect(layoutGraphHierarchy(request).positions.get("b")?.x).toBe(33);
    expect(layoutGraphHierarchy(input([], [])).positions.size).toBe(0);
  });

  it("rejects duplicate identifiers and invalid geometry", () => {
    expect(() => layoutGraphHierarchy(input(["a", "a"], []))).toThrow();
    expect(() => layoutGraphHierarchy({ ...input(["a"], []), columnGap: -1 })).toThrow();
    expect(() => layoutGraphHierarchy({ ...input(["a"], []), rowGap: Number.NaN })).toThrow();
    expect(() => layoutGraphHierarchy({ ...input(["a"], []), nodes: [{ id: "a", width: 0, height: 4 }] })).toThrow();
  });
});

it("keeps prior measurements immutable and ignores hidden or unchanged sizes", async () => {
  const { applyGraphNodeDimensionChanges } = await import("../src/lib/graph-layout");
  const original = new Map([["a", { width: 12, height: 6 }]]);
  expect(applyGraphNodeDimensionChanges(original, [
    { type: "dimensions", id: "a", dimensions: { width: 0, height: 0 } },
    { type: "dimensions", id: "b", dimensions: { width: Number.NaN, height: 6 } },
    { type: "dimensions", id: "a", dimensions: { width: 12, height: 6 } },
  ])).toBe(original);
  const changed = applyGraphNodeDimensionChanges(original, [{ type: "dimensions", id: "a", dimensions: { width: 18, height: 6 } }]);
  expect(changed.get("a")?.width).toBe(18);
  expect(original.get("a")?.width).toBe(12);
});
