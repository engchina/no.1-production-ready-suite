import type { GraphPoint } from "./graph-layout";

/** 矢印キーの向き。 */
export type GraphDirection = "left" | "right" | "up" | "down";

/**
 * キーボードで選択を移す先。`from` から `direction` の向きの ±60° の範囲にあるノードのうち、
 * 向きの成分を重く見た距離が最も近いものを返す（無ければ null）。
 */
export function nearestGraphNodeInDirection(
  points: ReadonlyArray<{ id: string } & GraphPoint>,
  from: GraphPoint,
  direction: GraphDirection,
): string | null {
  const axis = { left: [-1, 0], right: [1, 0], up: [0, -1], down: [0, 1] }[direction];
  let best: { id: string; score: number } | null = null;
  for (const point of points) {
    const dx = point.x - from.x;
    const dy = point.y - from.y;
    const along = dx * axis[0] + dy * axis[1];
    if (along <= 0) continue;
    const across = Math.abs(dx * axis[1] - dy * axis[0]);
    // tan 60° より横に外れたものは別の向き。
    if (across > along * Math.sqrt(3)) continue;
    const score = along + across * 2;
    if (!best || score < best.score) best = { id: point.id, score };
  }
  return best?.id ?? null;
}

/** 最初のキーボードの選択（左上に近いノード）。 */
export function firstGraphNode(points: ReadonlyArray<{ id: string } & GraphPoint>): string | null {
  let best: { id: string; score: number } | null = null;
  for (const point of points) {
    const score = point.y + point.x / 4;
    if (!best || score < best.score) best = { id: point.id, score };
  }
  return best?.id ?? null;
}

/**
 * 新しいノードの最初の位置。既にあるつながった相手の周りに、角度をずらして置く（配置の計算の出発点）。
 * つながる相手が無ければ、既存の範囲の右下の外。
 */
export function seedGraphPositions(
  added: readonly string[],
  edges: ReadonlyArray<{ source: string; target: string }>,
  existing: ReadonlyMap<string, GraphPoint>,
  spacing = 120,
): Map<string, GraphPoint> {
  const positions = new Map<string, GraphPoint>();
  const counts = new Map<string, number>();
  let right = 0, bottom = 0;
  for (const point of existing.values()) {
    right = Math.max(right, point.x);
    bottom = Math.max(bottom, point.y);
  }
  let orphan = 0;
  const addedSet = new Set(added);
  for (const id of added) {
    const anchor = edges.flatMap((edge) => {
      const other = edge.source === id ? edge.target : edge.target === id ? edge.source : null;
      return other && !addedSet.has(other) && existing.has(other) ? [other] : [];
    })[0];
    if (anchor) {
      const index = counts.get(anchor) ?? 0;
      counts.set(anchor, index + 1);
      const origin = existing.get(anchor)!;
      const angle = index * 2.399963; // 黄金角で重ならないように散らす
      const radius = spacing * (1 + Math.floor(index / 12) * 0.6);
      positions.set(id, { x: origin.x + Math.cos(angle) * radius, y: origin.y + Math.sin(angle) * radius });
    } else {
      positions.set(id, existing.size
        ? { x: right + spacing * (1 + (orphan % 5)), y: bottom + spacing * Math.floor(orphan / 5) }
        : { x: (orphan % 8) * spacing, y: Math.floor(orphan / 8) * spacing });
      orphan += 1;
    }
  }
  return positions;
}

/** 全角を 1、半角を 0.55 とした文字の幅（名前の行の長さの見積もり）。 */
function charWidth(char: string): number {
  return /[\u0000-ɏ -⁯｡-ﾟ]/u.test(char) ? 0.55 : 1;
}

const BREAK_AFTER = /[\s　、。，．・／/_\-–—:：;；)）\]】」』]/u;

/**
 * 節点の名前を `maxLines` 行に折り返す。1 行の幅は全角 `perLine` 字ぶん。語の切れ目（空白・句読点・記号）で
 * 折り返し、切れ目が無ければ幅で切る。収まらない分は最後の行を「…」で終える。完全な名前は詳細と hover で出す。
 */
export function wrapGraphLabel(text: string, perLine = 9, maxLines = 2): string {
  const chars = [...text.replace(/\s+/gu, " ").trim()];
  const lines: string[] = [];
  let index = 0;
  while (index < chars.length && lines.length < maxLines) {
    let width = 0;
    let end = index;
    let lastBreak = -1;
    while (end < chars.length && width + charWidth(chars[end]) <= perLine) {
      width += charWidth(chars[end]);
      if (BREAK_AFTER.test(chars[end])) lastBreak = end;
      end += 1;
    }
    if (end === index) end = index + 1;
    const last = lines.length === maxLines - 1;
    if (end < chars.length && !last && lastBreak >= index) end = lastBreak + 1;
    let line = chars.slice(index, end).join("").trim();
    if (last && end < chars.length) {
      const kept = [...line];
      while (kept.length && kept.reduce((sum, char) => sum + charWidth(char), 0) + 1 > perLine) kept.pop();
      line = kept.join("").trimEnd() + "…";
    }
    if (line) lines.push(line);
    index = end;
    while (chars[index] === " ") index += 1;
  }
  return lines.join("\n");
}

/**
 * 何も選ばなくてもすべての辺に種類の文字を出す上限（画面上の文字つきの辺の数）。これを超える図は、選んだ・
 * ホバーした節点につながる辺だけ文字を出し、ほかを薄くする（Neo4j Bloom の選択の強調。#5953）。全体を収めた
 * 画布（約 700×560px）に、種類の文字（約 30×15px）を辺や節点の名前と重ねずに置ける数の目安で、図の内容・
 * 関係の種類には合わせない。
 */
export const GRAPH_EDGE_LABELS_ALWAYS_MAX = 40;

/**
 * 配置のばねの強さ（fcose の `edgeElasticity`。既定は 0.45）。辺の数が節点の数を超える図は、ばねの力が斥力に
 * 勝って節点と名前が重なるので、密度（辺の数 ÷ 節点の数）の 1.5 乗で弱める。辺の数が節点の数までの図は既定の
 * まま（今までの配置）。合成の 25 節点で辺 56・112・224・448 本の図を配置し、名前を含む節点どうしが重ならなく
 * なる強さ（0.09・0.05・0.02・0.006）に合わせた（#5953）。
 */
export function graphEdgeElasticity(nodeCount: number, edgeCount: number): number {
  const density = nodeCount > 0 ? Math.max(1, edgeCount / nodeCount) : 1;
  return 0.45 / density ** 1.5;
}

/** 節点の名前の大きさ（座標）。 */
export const GRAPH_NODE_FONT_SIZE = 13;
/** 縮んだ図で、名前を画面上でこれより小さくしない（px）。 */
export const GRAPH_NAME_MIN_PX = 9;
/**
 * 名前を大きくする縮尺の段（下限の降順）。段の中では名前を「下限の縮尺で `GRAPH_NAME_MIN_PX`」の大きさにする。
 * 配置は 13px の名前に合わせた間隔なので、最後の値より縮むと名前どうしが重なって読めない。そこからは隠す（#5953）。
 */
export const GRAPH_NAME_STEPS = [0.55, 0.42];

/** 縮尺 → 名前の段。-1 = 元の大きさのまま、`GRAPH_NAME_STEPS.length` = 名前を隠す。 */
export function graphNameStep(zoom: number): number {
  if (zoom * GRAPH_NODE_FONT_SIZE >= GRAPH_NAME_MIN_PX) return -1;
  const index = GRAPH_NAME_STEPS.findIndex((lower) => zoom >= lower);
  return index < 0 ? GRAPH_NAME_STEPS.length : index;
}
