import { describe, expect, it } from "vitest";

import {
  bboxCoordinateModeFromMetadata,
  bboxPageAspectRatio,
  formatBboxPercent,
  normalizeBboxForPreview,
} from "./bbox";

describe("normalizeBboxForPreview", () => {
  it("normalizes ratio xyxy coordinates into preview percentages", () => {
    expect(normalizeBboxForPreview([0.25, 0.25, 0.5, 0.5])).toEqual({
      leftPercent: 25,
      topPercent: 25,
      widthPercent: 25,
      heightPercent: 25,
      unit: "ratio",
      coordinateMode: "xyxy",
    });
  });

  it("normalizes percent xyxy coordinates", () => {
    expect(normalizeBboxForPreview([25, 10, 50, 40])).toEqual({
      leftPercent: 25,
      topPercent: 10,
      widthPercent: 25,
      heightPercent: 30,
      unit: "percent",
      coordinateMode: "xyxy",
    });
  });

  it("keeps xywh coordinates when the third and fourth values are extents", () => {
    expect(normalizeBboxForPreview([0.25, 0.25, 0.2, 0.1])).toEqual({
      leftPercent: 25,
      topPercent: 25,
      widthPercent: 20,
      heightPercent: 10,
      unit: "ratio",
      coordinateMode: "xywh",
    });
  });

  it("clips xywh extents to the visible preview area", () => {
    expect(normalizeBboxForPreview([90, 90, 20, 20])).toEqual({
      leftPercent: 90,
      topPercent: 90,
      widthPercent: 10,
      heightPercent: 10,
      unit: "percent",
      coordinateMode: "xywh",
    });
  });

  it("uses explicit xywh mode instead of the numeric xyxy heuristic", () => {
    expect(normalizeBboxForPreview([25, 10, 50, 40], null, "xywh")).toEqual({
      leftPercent: 25,
      topPercent: 10,
      widthPercent: 50,
      heightPercent: 40,
      unit: "percent",
      coordinateMode: "xywh",
    });
  });

  it("normalizes absolute xyxy coordinates when page dimensions are available", () => {
    expect(normalizeBboxForPreview([153, 198, 306, 396], { width: 612, height: 792 })).toEqual({
      leftPercent: 25,
      topPercent: 25,
      widthPercent: 25,
      heightPercent: 25,
      unit: "absolute",
      coordinateMode: "xyxy",
    });
  });

  it("rejects absolute page coordinates without page dimensions", () => {
    expect(normalizeBboxForPreview([10, 20, 300, 400])).toBeNull();
  });

  it("formats preview percentages with one decimal", () => {
    expect(formatBboxPercent(25)).toBe("25.0");
    expect(formatBboxPercent(12.345)).toBe("12.3");
  });

  it("returns a stable page aspect ratio for locator overlays", () => {
    expect(bboxPageAspectRatio({ width: 612, height: 792 })).toBe("612 / 792");
    expect(bboxPageAspectRatio(null)).toBe("1 / 1.414");
  });
});

describe("bboxCoordinateModeFromMetadata", () => {
  it("reads supported bbox coordinate mode aliases", () => {
    expect(bboxCoordinateModeFromMetadata({ bbox_coordinate_mode: "x,y,width,height" })).toBe(
      "xywh"
    );
    expect(bboxCoordinateModeFromMetadata({ bbox_format: "x1_y1_x2_y2" })).toBe("xyxy");
    expect(bboxCoordinateModeFromMetadata({ coordinate_mode: "left-top-width-height" })).toBe(
      "xywh"
    );
  });

  it("ignores unknown or non-string bbox mode metadata", () => {
    expect(bboxCoordinateModeFromMetadata({ bbox_mode: "polygon" })).toBeNull();
    expect(bboxCoordinateModeFromMetadata({ bbox_mode: true })).toBeNull();
    expect(bboxCoordinateModeFromMetadata(null)).toBeNull();
  });
});
