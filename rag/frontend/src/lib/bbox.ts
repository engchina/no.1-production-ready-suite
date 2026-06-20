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
  rotation?: number | null;
};

const MIN_BBOX_VALUES = 4;
const DEFAULT_PAGE_ASPECT_RATIO = "1 / 1.414";
const BBOX_MODE_KEYS = [
  "bbox_mode",
  "bbox_coordinate_mode",
  "bbox_format",
  "coordinate_mode",
] as const;
const BBOX_UNIT_KEYS = [
  "bbox_unit",
  "bbox_coordinate_unit",
  "coordinate_unit",
  "unit",
] as const;
const BBOX_VALUE_KEYS = [
  "bbox",
  "bbox_json",
  "bbox_xyxy",
  "bbox_xywh",
  "bounding_box",
  "boundingBox",
  "polygon",
  "points",
  "coordinates",
] as const;
const PAGE_WIDTH_KEYS = ["page_width", "bbox_page_width", "source_page_width"] as const;
const PAGE_HEIGHT_KEYS = ["page_height", "bbox_page_height", "source_page_height"] as const;
const PAGE_ROTATION_KEYS = [
  "page_rotation",
  "bbox_page_rotation",
  "source_page_rotation",
  "rotation",
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
  preferredMode?: BboxCoordinateMode | null,
  preferredUnit?: BboxOverlayUnit | null
): BboxOverlayRect | null {
  if (!bbox || bbox.length < MIN_BBOX_VALUES) return null;

  const values = normalizeBboxValues(bbox);
  if (values.some((value) => !Number.isFinite(value))) return null;

  const max = Math.max(...values);
  const scale = preferredUnit
    ? resolveExplicitScale(preferredUnit, pageSize)
    : resolveScale(max, pageSize);
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

  return rotateOverlayRect(
    {
      leftPercent,
      topPercent,
      widthPercent,
      heightPercent,
      unit: scale.unit,
      coordinateMode,
    },
    pageSize?.rotation
  );
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
  if (metadata.bbox_xywh != null) return "xywh";
  if (metadata.bbox_xyxy != null) return "xyxy";
  for (const key of BBOX_VALUE_KEYS) {
    const inferred = inferBboxModeFromValue(metadata[key]);
    if (inferred) return inferred;
  }
  if (metadata.bbox_width != null || metadata.width != null || metadata.w != null) return "xywh";
  if (metadata.bbox_right != null || metadata.right != null || metadata.xmax != null) {
    return "xyxy";
  }
  return null;
}

export function bboxUnitFromMetadata(
  metadata?: Record<string, unknown> | null
): BboxOverlayUnit | null {
  if (!metadata) return null;
  for (const key of BBOX_UNIT_KEYS) {
    const value = metadata[key];
    const normalized = typeof value === "string" ? normalizeBboxUnitValue(value) : "";
    if (normalized) return normalized;
  }
  return null;
}

function rotateOverlayRect(
  rect: BboxOverlayRect,
  rotation?: number | null
): BboxOverlayRect {
  const normalized = normalizePageRotation(rotation);
  if (normalized === 90) {
    return {
      ...rect,
      leftPercent: clampPercent(rect.topPercent),
      topPercent: clampPercent(100 - rect.leftPercent - rect.widthPercent),
      widthPercent: clampExtent(rect.heightPercent, 100 - rect.topPercent),
      heightPercent: clampExtent(rect.widthPercent, rect.leftPercent + rect.widthPercent),
    };
  }
  if (normalized === 180) {
    return {
      ...rect,
      leftPercent: clampPercent(100 - rect.leftPercent - rect.widthPercent),
      topPercent: clampPercent(100 - rect.topPercent - rect.heightPercent),
    };
  }
  if (normalized === 270) {
    return {
      ...rect,
      leftPercent: clampPercent(100 - rect.topPercent - rect.heightPercent),
      topPercent: clampPercent(rect.leftPercent),
      widthPercent: clampExtent(rect.heightPercent, rect.topPercent + rect.heightPercent),
      heightPercent: clampExtent(rect.widthPercent, 100 - rect.leftPercent),
    };
  }
  return rect;
}

export function bboxPageRotationFromMetadata(
  metadata?: Record<string, unknown> | null
): number | null {
  if (!metadata) return null;
  for (const key of PAGE_ROTATION_KEYS) {
    const parsed = normalizePageRotation(metadata[key]);
    if (parsed != null) return parsed;
  }
  return null;
}

export function withBboxPageRotation(
  pageSize: BboxPageSize | null,
  rotation?: number | null
): BboxPageSize | null {
  const normalized = normalizePageRotation(rotation);
  if (!pageSize && normalized == null) return null;
  return { ...(pageSize ?? {}), rotation: normalized };
}

function normalizePageRotation(value: unknown): number | null {
  const parsed = numberValue(value);
  if (parsed == null || !Number.isInteger(parsed)) return null;
  const normalized = ((parsed % 360) + 360) % 360;
  return normalized === 0 || normalized === 90 || normalized === 180 || normalized === 270
    ? normalized
    : null;
}

export function bboxPageAspectRatio(pageSize?: BboxPageSize | null): string {
  const width = Number(
    pageSize?.rotation === 90 || pageSize?.rotation === 270
      ? pageSize?.height
      : pageSize?.width
  );
  const height = Number(
    pageSize?.rotation === 90 || pageSize?.rotation === 270
      ? pageSize?.width
      : pageSize?.height
  );
  if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) {
    return DEFAULT_PAGE_ASPECT_RATIO;
  }
  return `${width} / ${height}`;
}

export function bboxFromMetadata(metadata?: Record<string, unknown> | null): number[] | null {
  if (!metadata) return null;
  for (const key of BBOX_VALUE_KEYS) {
    const parsed = parseBboxValue(metadata[key]);
    if (parsed) return parsed;
  }
  const xyxy = numbersFromMetadata(metadata, [
    ["bbox_x1", "x1", "left", "xmin"],
    ["bbox_y1", "y1", "top", "ymin"],
    ["bbox_x2", "x2", "right", "xmax"],
    ["bbox_y2", "y2", "bottom", "ymax"],
  ]);
  if (xyxy) return xyxy;
  return numbersFromMetadata(metadata, [
    ["bbox_x", "x", "left"],
    ["bbox_y", "y", "top"],
    ["bbox_width", "width", "w"],
    ["bbox_height", "height", "h"],
  ]);
}

export function bboxPageSizeFromMetadata(
  metadata?: Record<string, unknown> | null
): BboxPageSize | null {
  if (!metadata) return null;
  const width = firstNumber(metadata, PAGE_WIDTH_KEYS);
  const height = firstNumber(metadata, PAGE_HEIGHT_KEYS);
  const rotation = bboxPageRotationFromMetadata(metadata);
  if (width && height) return { width, height, rotation };
  const pageSize = recordValue(metadata.page_size ?? metadata.pageSize ?? metadata.dimensions);
  const nestedWidth = numberValue(pageSize?.width);
  const nestedHeight = numberValue(pageSize?.height);
  const nestedRotation = bboxPageRotationFromMetadata(pageSize);
  if (nestedWidth && nestedHeight) {
    return { width: nestedWidth, height: nestedHeight, rotation: nestedRotation ?? rotation };
  }
  return rotation != null ? { rotation } : null;
}

function resolveExplicitScale(
  unit: BboxOverlayUnit,
  pageSize?: BboxPageSize | null
): { xFactor: number; yFactor: number; unit: BboxOverlayUnit } | null {
  if (unit === "ratio") return { xFactor: 100, yFactor: 100, unit };
  if (unit === "percent") return { xFactor: 1, yFactor: 1, unit };
  const width = Number(pageSize?.width);
  const height = Number(pageSize?.height);
  if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) {
    return null;
  }
  return { xFactor: 100 / width, yFactor: 100 / height, unit };
}

function parseBboxValue(value: unknown): number[] | null {
  if (Array.isArray(value)) return arrayBboxValue(value);
  if (typeof value === "string") {
    const trimmed = value.trim();
    if (!trimmed) return null;
    if (trimmed.startsWith("[")) {
      try {
        return parseBboxValue(JSON.parse(trimmed));
      } catch {
        return null;
      }
    }
    return numberArrayValue(trimmed.split(/[,\s]+/));
  }
  const source = recordValue(value);
  if (!source) return null;
  const polygon = polygonFromRecord(source);
  if (polygon) return polygon;
  const xyxy = numbersFromMetadata(source, [
    ["x1", "left", "xmin"],
    ["y1", "top", "ymin"],
    ["x2", "right", "xmax"],
    ["y2", "bottom", "ymax"],
  ]);
  if (xyxy) return xyxy;
  return numbersFromMetadata(source, [
    ["x", "left"],
    ["y", "top"],
    ["width", "w"],
    ["height", "h"],
  ]);
}

function inferBboxModeFromValue(value: unknown): BboxCoordinateMode | null {
  if (Array.isArray(value) && polygonArrayValue(value)) return "xyxy";
  const source = recordValue(value);
  if (!source) return null;
  if (polygonFromRecord(source)) return "xyxy";
  if (source.width != null || source.w != null) return "xywh";
  if (source.right != null || source.bottom != null || source.xmax != null || source.ymax != null) {
    return "xyxy";
  }
  return null;
}

function numbersFromMetadata(
  metadata: Record<string, unknown>,
  keyGroups: readonly (readonly string[])[]
): number[] | null {
  const values = keyGroups.map((keys) => firstNumber(metadata, keys));
  return values.every((value): value is number => value != null) ? values : null;
}

function firstNumber(
  metadata: Record<string, unknown>,
  keys: readonly string[]
): number | null {
  for (const key of keys) {
    const parsed = numberValue(metadata[key]);
    if (parsed != null) return parsed;
  }
  return null;
}

function numberArrayValue(value: unknown[]): number[] | null {
  if (value.length < MIN_BBOX_VALUES) return null;
  const numbers = value.slice(0, MIN_BBOX_VALUES).map(numberValue);
  return numbers.every((item): item is number => item != null) ? numbers : null;
}

function arrayBboxValue(value: unknown[]): number[] | null {
  const polygon = polygonArrayValue(value);
  if (polygon) return polygon;
  return numberArrayValue(value);
}

function normalizeBboxValues(value: unknown[]): number[] {
  const polygon = polygonArrayValue(value);
  if (polygon) return polygon;
  return value.slice(0, MIN_BBOX_VALUES).map(Number);
}

function polygonFromRecord(source: Record<string, unknown>): number[] | null {
  for (const key of ["polygon", "points", "coordinates", "vertices"] as const) {
    const value = source[key];
    if (Array.isArray(value)) {
      const polygon = polygonArrayValue(value);
      if (polygon) return polygon;
    }
  }
  return null;
}

function polygonArrayValue(value: unknown[]): number[] | null {
  const points = polygonPoints(value);
  if (points.length < 2) return null;
  const xs = points.map((point) => point[0]);
  const ys = points.map((point) => point[1]);
  return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
}

function polygonPoints(value: unknown[]): Array<[number, number]> {
  const nested = value
    .map((item): [number, number] | null => {
      if (!Array.isArray(item) || item.length < 2) return null;
      const x = numberValue(item[0]);
      const y = numberValue(item[1]);
      return x != null && y != null ? [x, y] : null;
    })
    .filter((point): point is [number, number] => point != null);
  if (nested.length >= 2) return nested;

  if (value.length < 8 || value.length % 2 !== 0) return [];
  const flatNumbers = value.map(numberValue);
  if (flatNumbers.some((item) => item == null)) return [];
  const points: Array<[number, number]> = [];
  for (let index = 0; index < flatNumbers.length; index += 2) {
    points.push([flatNumbers[index] as number, flatNumbers[index + 1] as number]);
  }
  return points;
}

function numberValue(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value !== "string") return null;
  const parsed = Number(value.trim());
  return Number.isFinite(parsed) ? parsed : null;
}

function recordValue(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
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

function normalizeBboxUnitValue(value: string): BboxOverlayUnit | null {
  const normalized = value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9%]+/g, "_")
    .replace(/^_+|_+$/g, "");
  if (
    normalized === "ratio" ||
    normalized === "relative" ||
    normalized === "normalized" ||
    normalized === "fraction"
  ) {
    return "ratio";
  }
  if (normalized === "percent" || normalized === "percentage" || normalized === "%") {
    return "percent";
  }
  if (
    normalized === "absolute" ||
    normalized === "pixel" ||
    normalized === "pixels" ||
    normalized === "px" ||
    normalized === "point" ||
    normalized === "points" ||
    normalized === "pt"
  ) {
    return "absolute";
  }
  return null;
}
