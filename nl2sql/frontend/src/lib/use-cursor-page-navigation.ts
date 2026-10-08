import { useCursorPages } from "@production-ready/ui";
import { useCallback, useEffect, useLayoutEffect, useRef } from "react";

interface LoadedPosition {
  cursor: string;
  depth: number;
}

const FIRST_PAGE: LoadedPosition = { cursor: "", depth: 0 };

/**
 * 命令的に一覧を取り直す画面（TanStack Query を使わない学習候補・フィードバック履歴）で、共通の
 * `useCursorPages` の「前へ / 次へ」を取り直しにつなぐ（#1266）。
 *
 * - cursor が変わったら `load(cursor)` を呼ぶ（絞り込みの変更・mount の読み込みは画面が自分で行い、
 *   `resetToFirstPage()` で先頭へ戻す）。
 * - 取り直しに失敗したら（`load` が false）、表示していたページへ戻す。前のデータを出したまま、同じボタンで
 *   もう一度進めるほか、失敗の案内の「再試行」は `retryNavigation()` で同じ移動をやり直せる。
 */
export function useCursorPageNavigation(load: (cursor: string) => Promise<boolean>) {
  const pages = useCursorPages();
  const { depth, next, prev, reset, canGoPrevious } = pages;
  const cursor = pages.cursor ?? "";
  // 表示しているデータの位置（取り直しの判定と、失敗したときに戻す先）。
  const loaded = useRef<LoadedPosition>(FIRST_PAGE);
  const failed = useRef<{ target: LoadedPosition; previous: LoadedPosition } | null>(null);
  const loadRef = useRef(load);
  useLayoutEffect(() => {
    loadRef.current = load;
  });

  useEffect(() => {
    if (loaded.current.cursor === cursor) return;
    const previous = loaded.current;
    const target: LoadedPosition = { cursor, depth };
    loaded.current = target;
    failed.current = null;
    void loadRef.current(cursor).then((ok) => {
      // 後から別の移動・絞り込みが始まっていたら、その結果に任せる。
      if (ok || loaded.current !== target) return;
      loaded.current = previous;
      failed.current = { target, previous };
      if (target.depth > previous.depth) prev();
      else next(previous.cursor);
    });
  }, [cursor, depth, next, prev]);

  const resetToFirstPage = useCallback(() => {
    loaded.current = FIRST_PAGE;
    failed.current = null;
    reset();
  }, [reset]);

  const retryNavigation = useCallback(() => {
    const last = failed.current;
    if (!last) return false;
    failed.current = null;
    if (last.target.depth > last.previous.depth) next(last.target.cursor);
    else prev();
    return true;
  }, [next, prev]);

  return { cursor, depth, canGoPrevious, next, prev, resetToFirstPage, retryNavigation };
}
