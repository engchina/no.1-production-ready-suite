import { describe, expect, it } from "vitest";

import {
  GRAPH_EDGE_LABELS_ALWAYS_MAX,
  GRAPH_NAME_STEPS,
  firstGraphNode,
  graphEdgeElasticity,
  graphNameStep,
  nearestGraphNodeInDirection,
  seedGraphPositions,
  wrapGraphLabel,
} from "../src/lib/graph-explorer-model";

describe("wrapGraphLabel", () => {
  it("wraps at word gaps into at most two lines and ends the overflow with an ellipsis", () => {
    expect(wrapGraphLabel("合成資料 A 共通規則.md")).toBe("合成資料 A\n共通規則.md");
    expect(wrapGraphLabel("短い名前")).toBe("短い名前");
    const long = wrapGraphLabel("原文 12：合成の本文の先頭にある長い説明の文");
    expect(long.split("\n")).toHaveLength(2);
    expect(long.endsWith("…")).toBe(true);
  });

  it("breaks text without gaps by width", () => {
    const lines = wrapGraphLabel("あいうえおかきくけこさしすせそたちつてと").split("\n");
    expect(lines[0]).toBe("あいうえおかきくけ");
    expect(lines[1].endsWith("…")).toBe(true);
  });
});

describe("nearestGraphNodeInDirection", () => {
  const points = [
    { id: "right", x: 100, y: 10 },
    { id: "down", x: 0, y: 100 },
    { id: "far-right", x: 300, y: 0 },
  ];
  it("picks the nearest node in the cone of the arrow key", () => {
    expect(nearestGraphNodeInDirection(points, { x: 0, y: 0 }, "right")).toBe("right");
    expect(nearestGraphNodeInDirection(points, { x: 0, y: 0 }, "down")).toBe("down");
    expect(nearestGraphNodeInDirection(points, { x: 0, y: 0 }, "left")).toBeNull();
  });
  it("starts from the top-left node", () => {
    expect(firstGraphNode(points)).toBe("right");
    expect(firstGraphNode([{ id: "a", x: 0, y: 50 }, { id: "b", x: 40, y: 0 }])).toBe("b");
  });
});

describe("seedGraphPositions", () => {
  it("places added nodes around their connected existing node and keeps them apart", () => {
    const existing = new Map([["root", { x: 0, y: 0 }]]);
    const seeds = seedGraphPositions(["a", "b", "orphan"], [{ source: "root", target: "a" }, { source: "b", target: "root" }], existing);
    const a = seeds.get("a")!, b = seeds.get("b")!;
    expect(Math.hypot(a.x, a.y)).toBeCloseTo(120, 5);
    expect(Math.hypot(a.x - b.x, a.y - b.y)).toBeGreaterThan(40);
    expect(seeds.get("orphan")!.x).toBeGreaterThan(0);
  });
});

describe("graphEdgeElasticity", () => {
  it("keeps the default spring for graphs with no more edges than nodes", () => {
    expect(graphEdgeElasticity(25, 6)).toBe(0.45);
    expect(graphEdgeElasticity(25, 25)).toBe(0.45);
    expect(graphEdgeElasticity(0, 0)).toBe(0.45);
  });

  it("weakens the spring as the edge density grows so that nodes and names keep their distance", () => {
    // 合成の 25 節点で、名前を含む節点どうしが重ならなくなった強さ（56 本: 0.2 未満、448 本: 0.006 前後）。
    expect(graphEdgeElasticity(25, 56)).toBeLessThan(0.2);
    expect(graphEdgeElasticity(25, 448)).toBeCloseTo(0.006, 3);
    expect(graphEdgeElasticity(25, 448)).toBeLessThan(graphEdgeElasticity(25, 224));
  });
});

describe("GRAPH_EDGE_LABELS_ALWAYS_MAX", () => {
  it("is a count of labelled edges on screen, well below a dense document graph", () => {
    expect(GRAPH_EDGE_LABELS_ALWAYS_MAX).toBeGreaterThanOrEqual(24);
    expect(GRAPH_EDGE_LABELS_ALWAYS_MAX).toBeLessThan(448);
  });
});

describe("graphNameStep", () => {
  it("keeps the base size while readable, enlarges per step, and hides below the last step", () => {
    expect(graphNameStep(0.8)).toBe(-1);
    expect(graphNameStep(0.6)).toBe(0);
    expect(graphNameStep(0.43)).toBe(1);
    expect(graphNameStep(0.41)).toBe(GRAPH_NAME_STEPS.length);
  });
});
