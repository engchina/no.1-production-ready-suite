export type BboxOverlayUnit = "ratio" | "percent" | "absolute";
export type BboxCoordinateMode = "xyxy" | "xywh";

export type BboxOverlayRect = {
  leftPercent: number;
  topPercent: number;
  widthPercent: number;
  heightPercent: number;
  unit: BboxOverlayUnit;
  coordinateMode: BboxCoordinateMode;
};

export type BboxPageSize = {
  width?: number | null;
  height?: number | null;
};

const MIN_BBOX_VALUES = 4;
const DEFAULT_PAGE_ASPECT_RATIO = "1 / 1.414";
const BBOX_MODE_KEYS = [
  "bbox_mode",
  "bbox_coordinate_mode",
  "bbox_format",
  "coordinate_mode",
] as const;

function clampPercent(value: number): number {
  return Math.max(0, Math.min(100, value));
}

function clampExtent(value: number, remainingPercent: number): number {
  return Math.max(0, Math.min(value, remainingPercent));
}

export function formatBboxPercent(value: number): string {
  return value.toFixed(1);
}

export function normalizeBboxForPreview(
  bbox?: number[] | null,
  pageSize?: BboxPageSize | null,
  preferredMode?: BboxCoordinateMode | null
): BboxOverlayRect | null {
  if (!bbox || bbox.length < MIN_BBOX_VALUES) return null;

  const values = bbox.slice(0, MIN_BBOX_VALUES).map(Number);
  if (values.some((value) => !Number.isFinite(value))) return null;

  const max = Math.max(...values);
  const scale = resolveScale(max, pageSize);
  if (!scale) return null;

  const rawLeft = values[0] * scale.xFactor;
  const rawTop = values[1] * scale.yFactor;
  const rawRightOrWidth = values[2] * scale.xFactor;
  const rawBottomOrHeight = values[3] * scale.yFactor;
  const leftPercent = clampPercent(rawLeft);
  const topPercent = clampPercent(rawTop);
  const coordinateMode: BboxCoordinateMode =
    preferredMode ??
    (rawRightOrWidth > rawLeft && rawBottomOrHeight > rawTop ? "xyxy" : "xywh");

  const widthPercent =
    coordinateMode === "xyxy"
      ? clampExtent(clampPercent(rawRightOrWidth) - leftPercent, 100 - leftPercent)
      : clampExtent(rawRightOrWidth, 100 - leftPercent);
  const heightPercent =
    coordinateMode === "xyxy"
      ? clampExtent(clampPercent(rawBottomOrHeight) - topPercent, 100 - topPercent)
      : clampExtent(rawBottomOrHeight, 100 - topPercent);

  if (widthPercent <= 0 || heightPercent <= 0) return null;

  return {
    leftPercent,
    topPercent,
    widthPercent,
    heightPercent,
    unit: scale.unit,
    coordinateMode,
  };
}

export function bboxCoordinateModeFromMetadata(
  metadata?: Record<string, unknown> | null
): BboxCoordinateMode | null {
  if (!metadata) return null;
  for (const key of BBOX_MODE_KEYS) {
    const value = metadata[key];
    const normalized = typeof value === "string" ? normalizeBboxModeValue(value) : "";
    if (normalized) return normalized;
  }
  return null;
}

export function bboxPageAspectRatio(pageSize?: BboxPageSize | null): string {
  const width = Number(pageSize?.width);
  const height = Number(pageSize?.height);
  if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) {
    return DEFAULT_PAGE_ASPECT_RATIO;
  }
  return `${width} / ${height}`;
}

function resolveScale(
  maxValue: number,
  pageSize?: BboxPageSize | null
): { xFactor: number; yFactor: number; unit: BboxOverlayUnit } | null {
  if (maxValue <= 1) return { xFactor: 100, yFactor: 100, unit: "ratio" };
  if (maxValue <= 100) return { xFactor: 1, yFactor: 1, unit: "percent" };

  const width = Number(pageSize?.width);
  const height = Number(pageSize?.height);
  if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) {
    return null;
  }

  return { xFactor: 100 / width, yFactor: 100 / height, unit: "absolute" };
}

function normalizeBboxModeValue(value: string): BboxCoordinateMode | null {
  const normalized = value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
  if (normalized === "xyxy" || normalized === "x1_y1_x2_y2") return "xyxy";
  if (
    normalized === "xywh" ||
    normalized === "x_y_width_height" ||
    normalized === "left_top_width_height"
  ) {
    return "xywh";
  }
  return null;
}
