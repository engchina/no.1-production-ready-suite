import { useEffect, useState } from "react";

/**
 * 入力値を遅延させて返す。検索欄の 1 打鍵ごとに API を叩かないための共通 hook。
 * 管理系一覧ページはこの hook を通した値を query key / query params に渡す。
 */
export function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timeoutId = window.setTimeout(() => setDebounced(value), delayMs);
    return () => window.clearTimeout(timeoutId);
  }, [delayMs, value]);
  return debounced;
}

/** 管理系一覧の検索欄で共有する既定のデバウンス時間(ms)。 */
export const LIST_SEARCH_DEBOUNCE_MS = 250;
