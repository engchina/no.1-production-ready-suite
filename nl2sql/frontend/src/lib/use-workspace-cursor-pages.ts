import { useCallback, useMemo } from "react";

import { useWorkspaceState } from "@/components/WorkspaceState";

/**
 * カーソル型の API の「前へ / 次へ」の位置（前へ戻るためのカーソルの列）を作業状態に残す（#1764）。
 *
 * 共通の `useCursorPages` と同じ形（cursor / depth / next / prev / reset）で、再読込・画面の移動のあとも
 * 同じページに戻る（UX 契約 workspace-state.md「検索・フィルター・ソート・ページ」）。チャットの会話の履歴
 * （`historyCursors`。#1265）と同じく、積んだカーソルの列だけを保存する。
 */
export function useWorkspaceCursorPages(field: string) {
  const [stored, setStored] = useWorkspaceState<string[]>(field, []);
  // 保存の形が壊れていても（古い版・手で書き換えた値）先頭のページとして扱う。
  const cursors = useMemo(
    () => (Array.isArray(stored) ? stored.filter((value): value is string => typeof value === "string" && value !== "") : []),
    [stored]
  );
  const next = useCallback((nextCursor: string) => setStored((current) => [...current, nextCursor]), [setStored]);
  const prev = useCallback(() => setStored((current) => current.slice(0, -1)), [setStored]);
  const reset = useCallback(() => setStored((current) => (current.length === 0 ? current : [])), [setStored]);
  return {
    cursor: cursors.at(-1) ?? "",
    depth: cursors.length,
    canGoPrevious: cursors.length > 0,
    next,
    prev,
    reset,
  };
}

export type WorkspaceCursorPages = ReturnType<typeof useWorkspaceCursorPages>;
