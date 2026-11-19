import { describe, expect, it } from "vitest";

import { GRAPH_READABLE_MIN_ZOOM, graphBounds, graphReadableViewport } from "../src/lib/graph-viewport";

describe("graphBounds", () => {
  it("returns the rectangle containing every node, or null when empty", () => {
    expect(graphBounds([])).toBeNull();
    expect(graphBounds([{ x: 10, y: 20, width: 100, height: 50 }, { x: -5, y: 80, width: 30, height: 10 }])).toEqual({ x: -5, y: 20, width: 115, height: 70 });
  });
});

describe("graphReadableViewport", () => {
  const frame = { width: 1000, height: 600 };

  it("centers a small range without zooming past the upper limit", () => {
    const viewport = graphReadableViewport({ bounds: { x: 0, y: 0, width: 200, height: 100 }, frame });
    expect(viewport.zoom).toBe(1);
    expect(viewport.x).toBe(400);
    expect(viewport.y).toBe(250);
  });

  it("shrinks to fit only down to the minimum zoom", () => {
    const bounds = { x: 0, y: 0, width: 1000, height: 100 };
    expect(graphReadableViewport({ bounds, frame, padding: 50 }).zoom).toBeCloseTo(0.9, 5);
    const wide = { x: 0, y: 0, width: 1904, height: 100 };
    expect(graphReadableViewport({ bounds: wide, frame, padding: 48 }).zoom).toBe(GRAPH_READABLE_MIN_ZOOM);
    expect(graphReadableViewport({ bounds: wide, frame, padding: 48, minZoom: 0.4 }).zoom).toBeCloseTo(904 / 1904, 5);
  });

  it("keeps the readable minimum and aligns the overflowing axis to the requested edge", () => {
    const bounds = { x: 100, y: 50, width: 4000, height: 3000 };
    const start = graphReadableViewport({ bounds, frame, padding: 20 });
    expect(start.zoom).toBe(GRAPH_READABLE_MIN_ZOOM);
    expect(start.x).toBe(20 - 100 * GRAPH_READABLE_MIN_ZOOM);
    expect(start.y).toBe(20 - 50 * GRAPH_READABLE_MIN_ZOOM);
    const end = graphReadableViewport({ bounds, frame, padding: 20, align: { x: "end" } });
    expect(end.x).toBe(1000 - 20 - 4100 * GRAPH_READABLE_MIN_ZOOM);
    expect(end.y).toBe(start.y);
  });

  it("keeps per-side padding clear (for controls over the canvas)", () => {
    const padding = { top: 64, right: 24, bottom: 24, left: 24 };
    const small = graphReadableViewport({ bounds: { x: 0, y: 0, width: 200, height: 100 }, frame, padding });
    expect(small.y).toBe(64 + (600 - 88) / 2 - 50);
    const tall = graphReadableViewport({ bounds: { x: 0, y: 10, width: 200, height: 5000 }, frame, padding });
    expect(tall.y).toBe(64 - 10 * GRAPH_READABLE_MIN_ZOOM);
  });

  it("centers the axis that fits while the other axis overflows", () => {
    const viewport = graphReadableViewport({ bounds: { x: 0, y: 0, width: 200, height: 2000 }, frame, padding: 20 });
    expect(viewport.zoom).toBe(GRAPH_READABLE_MIN_ZOOM);
    expect(viewport.x).toBe(500 - 100 * GRAPH_READABLE_MIN_ZOOM);
    expect(viewport.y).toBe(20);
  });
});
