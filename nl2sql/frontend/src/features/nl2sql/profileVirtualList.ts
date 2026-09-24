/** 許可オブジェクト選択リストの仮想スクロール計算(純粋ロジック)。 */

/**
 * 1 行の高さ(px)。`SchemaObjectOption` の固定高と必ず一致させる。
 * 定数と実 DOM の高さがずれるとスクロール中に行が飛び、末尾に空白が残る。
 */
export const SCHEMA_OPTION_ROW_HEIGHT = 44;

/** 仮想スクロールのビューポート高さ(px)。 */
export const SCHEMA_OPTION_VIEWPORT_HEIGHT = 320;

/** ビューポート外に先読みする行数(上下それぞれ)。 */
const OVERSCAN_ROWS = 5;

export interface SchemaOptionWindow {
  /** 描画開始 index。 */
  start: number;
  /** 描画終了 index(排他)。 */
  end: number;
  /** 描画ブロックの translateY(px)。 */
  offset: number;
  /** スペーサの総高さ(px)。 */
  totalHeight: number;
}

/** scrollTop から描画すべき行範囲を求める。 */
export function schemaOptionWindow(
  scrollTop: number,
  count: number,
  rowHeight = SCHEMA_OPTION_ROW_HEIGHT,
  viewportHeight = SCHEMA_OPTION_VIEWPORT_HEIGHT
): SchemaOptionWindow {
  const safeScrollTop = Number.isFinite(scrollTop) ? Math.max(0, scrollTop) : 0;
  const visibleCount = Math.ceil(viewportHeight / rowHeight) + OVERSCAN_ROWS * 2;
  // 実スクロール可能域を超える scrollTop(慣性スクロール・件数減少直後)でも
  // 空の window を返さないよう末尾側でクランプする。
  const start = Math.min(
    Math.max(0, Math.floor(safeScrollTop / rowHeight) - OVERSCAN_ROWS),
    Math.max(0, count - visibleCount)
  );
  return {
    start,
    end: Math.min(count, start + visibleCount),
    offset: start * rowHeight,
    totalHeight: count * rowHeight,
  };
}
