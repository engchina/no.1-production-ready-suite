import { describe, expect, it } from "vitest";
import {
  applyGraphNodePositionChanges,
  graphEdgeStyle,
  graphNeighborIds,
  graphParallelEdgeGeometry,
  selectGraphEdgeHandles,
} from "../src/lib/graph-edges";
import { applyGraphNodeDimensionChanges } from "../src/lib/graph-layout";

describe("selectGraphEdgeHandles", () => {
  it("縦優勢で上下・横優勢で左右のハンドルを選ぶ", () => {
    expect(selectGraphEdgeHandles({ x: 100, y: 0 }, { x: 120, y: 300 })).toEqual({ sourceHandle: "s-bottom", targetHandle: "t-top", orientation: "vertical" });
    expect(selectGraphEdgeHandles({ x: 0, y: 300 }, { x: 0, y: 0 })).toEqual({ sourceHandle: "s-top", targetHandle: "t-bottom", orientation: "vertical" });
    expect(selectGraphEdgeHandles({ x: 0, y: 10 }, { x: 400, y: 0 })).toEqual({ sourceHandle: "s-right", targetHandle: "t-left", orientation: "horizontal" });
    expect(selectGraphEdgeHandles({ x: 400, y: 0 }, { x: 0, y: 10 }).sourceHandle).toBe("s-left");
  });

  it("preferVertical は dx 優勢でも縦を選び、dy=0 は横のまま", () => {
    expect(selectGraphEdgeHandles({ x: 0, y: 0 }, { x: 600, y: 116 }, { preferVertical: true }).orientation).toBe("vertical");
    expect(selectGraphEdgeHandles({ x: 0, y: 0 }, { x: 600, y: 0 }, { preferVertical: true }).orientation).toBe("horizontal");
  });
});

describe("applyGraphNodePositionChanges", () => {
  it("position change だけを反映し、変更が無ければ同じ Map を返す", () => {
    const initial = new Map([["a", { x: 1, y: 2 }]]);
    const applied = applyGraphNodePositionChanges(initial, [
      { type: "position", id: "b", position: { x: 10, y: 20 } },
      { type: "select", id: "a" },
      { type: "position", id: "c", position: { x: Number.NaN, y: 0 } },
    ]);
    expect(applied).not.toBe(initial);
    expect(applied.get("b")).toEqual({ x: 10, y: 20 });
    expect(applied.has("c")).toBe(false);
    expect(applyGraphNodePositionChanges(initial, [{ type: "select", id: "a" }])).toBe(initial);
  });
});

describe("applyGraphNodeDimensionChanges", () => {
  it("実測サイズを蓄積し、同値なら同じ Map を返す", () => {
    const applied = applyGraphNodeDimensionChanges(new Map(), [{ type: "dimensions", id: "a", dimensions: { width: 10, height: 20 } }]);
    expect(applied.get("a")).toEqual({ width: 10, height: 20 });
    expect(applyGraphNodeDimensionChanges(applied, [{ type: "dimensions", id: "a", dimensions: { width: 10, height: 20 } }])).toBe(applied);
  });
});

describe("graphParallelEdgeGeometry", () => {
  it("法線方向の弧とラベルの座標を返す", () => {
    const straight = graphParallelEdgeGeometry({ x: 0, y: 0 }, { x: 100, y: 0 }, 0);
    expect(straight).toMatchObject({ labelX: 50, labelY: 0 });
    const arced = graphParallelEdgeGeometry({ x: 0, y: 0 }, { x: 100, y: 0 }, 10);
    expect(arced.labelY).toBeCloseTo(10);
    expect(arced.path).toContain("Q 50,20");
  });
});

describe("graphNeighborIds", () => {
  it("ホバーのノードと、直接つながるノードだけを返す", () => {
    const edges = [{ source: "a", target: "b" }, { source: "c", target: "a" }, { source: "c", target: "d" }];
    expect(graphNeighborIds(null, edges)).toBeNull();
    expect(graphNeighborIds("a", edges)).toEqual(new Set(["a", "b", "c"]));
  });
});

describe("graphEdgeStyle", () => {
  it("強調は色と太さで示し、破線と透明度を保つ", () => {
    expect(graphEdgeStyle({ dash: "4 3", opacity: 0.35 })).toMatchObject({ stroke: "var(--color-graph-line)", strokeWidth: 1.25, strokeDasharray: "4 3", opacity: 0.35 });
    expect(graphEdgeStyle({ stroke: "var(--x)", emphasized: true })).toMatchObject({ stroke: "var(--color-accent-fg)", strokeWidth: 2.5 });
  });
});
