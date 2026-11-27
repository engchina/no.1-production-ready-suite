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
