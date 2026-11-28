import type { GraphNodeDimensions, GraphPoint } from "./graph-layout";

/**
 * 節点の文字が読める縮尺の下限。共有の節点（`GraphNodeCard`）の 14px の名前が 11px 前後で読める大きさ。
 * 範囲を合わせる表示で、これより小さくして全体を収めない（収まらない分は図を動かして見る。#5520）。
 */
export const GRAPH_READABLE_MIN_ZOOM = 0.8;

/**
 * 利用者が手で縮めるときの下限（総覧）。範囲を合わせる表示（全体表示・選択に合わせる・初期表示）は
 * この値ではなく `GRAPH_READABLE_MIN_ZOOM` を下限にする（#5520・#5908）。
 */
export const GRAPH_OVERVIEW_MIN_ZOOM = 0.1;

export interface GraphRect extends GraphPoint, GraphNodeDimensions {}

export interface GraphViewport extends GraphPoint {
  zoom: number;
}

/** 画布の内側の余白（画面の px）。画布の上に重ねた操作（右上の拡大・縮小など）の分を `top` で空ける。 */
export type GraphViewportPadding = number | { top: number; right: number; bottom: number; left: number };

/** 節点の矩形をすべて含む矩形。空なら null。 */
export function graphBounds(rects: Iterable<GraphRect>): GraphRect | null {
  let left = Infinity, top = Infinity, right = -Infinity, bottom = -Infinity;
  for (const rect of rects) {
    left = Math.min(left, rect.x);
    top = Math.min(top, rect.y);
    right = Math.max(right, rect.x + rect.width);
    bottom = Math.max(bottom, rect.y + rect.height);
  }
  return Number.isFinite(left) ? { x: left, y: top, width: right - left, height: bottom - top } : null;
}

/** 下限の縮尺で収まらない向きの合わせ方（start = 左・上の端、end = 右・下の端、center = 範囲の中央）。 */
export type GraphViewportAlign = "start" | "end" | "center";

/**
 * 範囲を画布に合わせる表示（React Flow の viewport）。縮尺は `minZoom`〜`maxZoom` に収め、
 * 下限で収まらない向きは `align` の端（start = 左・上、end = 右・下）を画布の端にそろえるか、範囲の中央
 * （center）を画布の中央に置く。収まる向きは中央。
 */
export function graphReadableViewport({
  bounds,
  frame,
  padding: paddingInput = 24,
  minZoom = GRAPH_READABLE_MIN_ZOOM,
  maxZoom = 1,
  align = {},
}: {
  bounds: GraphRect;
  frame: GraphNodeDimensions;
  padding?: GraphViewportPadding;
  minZoom?: number;
  maxZoom?: number;
  align?: { x?: GraphViewportAlign; y?: GraphViewportAlign };
}): GraphViewport {
  const padding = typeof paddingInput === "number" ? { top: paddingInput, right: paddingInput, bottom: paddingInput, left: paddingInput } : paddingInput;
  const room = { width: Math.max(1, frame.width - padding.left - padding.right), height: Math.max(1, frame.height - padding.top - padding.bottom) };
  const fit = Math.min(room.width / Math.max(1, bounds.width), room.height / Math.max(1, bounds.height));
  const zoom = Math.min(maxZoom, Math.max(minZoom, fit));
  // 収まる向きは余白の内側の中央、収まらない向きは指定の端をそろえる。
  const place = (start: number, length: number, before: number, roomLength: number, side: GraphViewportAlign) => {
    if (length * zoom <= roomLength || side === "center") return before + roomLength / 2 - (start + length / 2) * zoom;
    return side === "end" ? before + roomLength - (start + length) * zoom : before - start * zoom;
  };
  return {
    x: place(bounds.x, bounds.width, padding.left, room.width, align.x ?? "start"),
    y: place(bounds.y, bounds.height, padding.top, room.height, align.y ?? "start"),
    zoom,
  };
}
