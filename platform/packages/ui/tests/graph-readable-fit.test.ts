import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import { GRAPH_OVERVIEW_MIN_ZOOM, GRAPH_READABLE_MIN_ZOOM } from "../src/lib/graph-viewport";

// 範囲を合わせる表示は読める縮尺を下限にし、極小の縮尺で全体を収めない（#5520・#5908）。
describe("graph readable fit contract", () => {
  it("keeps the manual overview floor below the readable minimum", () => {
    expect(GRAPH_OVERVIEW_MIN_ZOOM).toBeGreaterThan(0);
    expect(GRAPH_OVERVIEW_MIN_ZOOM).toBeLessThan(GRAPH_READABLE_MIN_ZOOM);
  });

  it("does not fit the shared canvas with an unbounded zoom", () => {
    const source = readFileSync(resolve(import.meta.dirname, "../src/components/graph/graph-canvas.tsx"), "utf8");
    expect(source).not.toMatch(/minZoom:\s*0\b/);
    expect(source).not.toContain("Number.EPSILON");
    expect(source).not.toMatch(/\.fitView\(/);
  });
});
