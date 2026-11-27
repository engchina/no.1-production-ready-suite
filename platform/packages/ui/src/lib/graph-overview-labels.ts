import type { ReactFlowState } from "@xyflow/react";

interface Box {
  left: number;
  top: number;
  right: number;
  bottom: number;
}

interface Anchor {
  x: number;
  y: number;
  width: number;
  height: number;
}

const GAP = 3;
const clamp = (value: number, min: number, max: number) => Math.min(Math.max(value, min), max);

/** 選択した名称から順に、点と他の名称に重ならない最寄りの位置を画面座標で探す。 */
export function placeOverviewLabels(anchors: readonly Anchor[], viewport: Box): Box[] {
  const occupied: Box[] = anchors.map(({ x, y }) => ({ left: x - GAP, right: x + GAP, top: y - GAP, bottom: y + GAP }));
  return anchors.map(({ x, y, width, height }) => {
    const natural = x - width / 2;
    const minX = viewport.left + GAP, maxX = Math.max(minX, viewport.right - width - GAP);
    const minY = viewport.top + GAP, maxY = Math.max(minY, viewport.bottom - height - GAP);
    let left = clamp(natural, minX, maxX);
    let top = clamp(y - height - 2 * GAP, minY, maxY);
    let nearest = Infinity;
    for (const candidate of [natural, x + 2 * GAP, x - width - 2 * GAP]) {
      const candidateLeft = clamp(candidate, minX, maxX);
      const preferred = candidate === natural ? y - height - 2 * GAP : y - height / 2;
      const column = occupied.filter(box => candidateLeft < box.right + GAP && candidateLeft + width + GAP > box.left)
        .sort((a, b) => a.top - b.top);
      // この列の空き区間だけを調べ、名称を点の近くに置く。
      let start = minY;
      function tryGap(end: number) {
        if (start > end) return;
        const candidateTop = clamp(preferred, start, end);
        const dx = x - clamp(x, candidateLeft, candidateLeft + width);
        const dy = y - clamp(y, candidateTop, candidateTop + height);
        const distance = dx * dx + dy * dy;
        if (distance < nearest) {
          nearest = distance;
          left = candidateLeft;
          top = candidateTop;
        }
      }
      for (const box of column) {
        tryGap(Math.min(maxY, box.top - GAP - height));
        start = Math.max(start, box.bottom + GAP);
        if (start > maxY) break;
      }
      tryGap(maxY);
    }
    const placed = { left, top, right: left + width, bottom: top + height };
    occupied.push(placed);
    return placed;
  });
}

export function arrangeOverviewLabels(canvas: HTMLElement, state: Pick<ReactFlowState, "nodeLookup" | "transform">) {
  const bounds = { left: 0, top: 0, right: canvas.clientWidth, bottom: canvas.clientHeight };
  const [panX, panY, zoom] = state.transform;
  const items = Array.from(canvas.querySelectorAll<HTMLElement>(".graph-overview-label")).flatMap(label => {
    const marker = label.parentElement;
    const frame = marker?.closest<HTMLElement>(".react-flow__node");
    const node = state.nodeLookup.get(frame?.dataset.id ?? "");
    if (!marker || !frame || !node) return [];
    // 全体表示の直後は DOM の変換が前の描画フレームの場合がある。同じ縮尺で全節点を投影する。
    const { positionAbsolute } = node.internals;
    const x = panX + (positionAbsolute.x + frame.offsetWidth / 2) * zoom;
    const y = panY + (positionAbsolute.y + frame.offsetHeight / 2) * zoom;
    if (x < 0 || x > bounds.right || y < 0 || y > bounds.bottom) return [];
    return [{ marker, x, y, width: label.offsetWidth, height: label.offsetHeight }];
  }).sort((a, b) => Number(b.marker.hasAttribute("data-selected")) - Number(a.marker.hasAttribute("data-selected")));
  const placements = placeOverviewLabels(items, bounds);
  items.forEach(({ marker, x, y, width }, index) => {
    const box = placements[index];
    marker.style.setProperty("--graph-label-x", (box.left + width / 2 - x) + "px");
    marker.style.setProperty("--graph-label-y", (box.top - y) + "px");
    const dx = clamp(x, box.left, box.right) - x, dy = clamp(y, box.top, box.bottom) - y;
    const length = Math.hypot(dx, dy);
    marker.style.setProperty("--graph-leader", (length > 9 ? length - GAP : 0) + "px");
    marker.style.setProperty("--graph-leader-angle", Math.atan2(dy, dx) + "rad");
  });
}
