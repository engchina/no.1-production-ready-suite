import { useCallback, useState } from "react";

import { useTraceProgressSnapshot, type TraceProgressSnapshot } from "@production-ready/ui";

import { apiGet } from "@/lib/api";
import { API_TIMEOUT_MS } from "@/lib/requestPolicy";
import { CHAT_PROGRESS_STEP_DEFINITIONS, parseTraceProgressResponse } from "./chatProgress";

export interface Nl2SqlTraceProgress extends TraceProgressSnapshot {
  /** 書けなかった span がある（処理の経過に「一部の処理だけを記録しています。」を出す）。 */
  partial: boolean;
}

/**
 * 記録（ジョブ・実行履歴の行・DB 管理チャットのターン）の処理の経過（#3226 の案 A）。
 *
 * span は共通の span の表にあり、記録ごとの API（`path`。記録の判定を通した後に、その記録の trace id で読む）が
 * span の段のイベントの一覧（snapshot）を返す。実行中（`active`）は共有の `useTraceProgressSnapshot` が読み直す。
 * `targetId` はイベントの `target_id`（記録の ID）。`path` が null なら読まない。
 * 一部だけの記録（`partial`）は古い形式（`legacy`）より優先する（span が 1 つも書けなかった実行を古い形式と出さない）。
 */
export function useNl2SqlTraceProgress({
  targetId,
  path,
  active,
}: {
  targetId: string;
  path: string | null;
  active: boolean;
}): Nl2SqlTraceProgress {
  const [dropped, setDropped] = useState<{ path: string; count: number } | null>(null);
  const fetchEvents = useCallback(
    async (signal: AbortSignal) => {
      if (!path) return [];
      const data = await apiGet<unknown>(path, { signal, timeoutMs: API_TIMEOUT_MS.interactiveDetail });
      const parsed = parseTraceProgressResponse(data);
      setDropped({ path, count: parsed.droppedSpans });
      return parsed.events;
    },
    [path],
  );
  const snapshot = useTraceProgressSnapshot({
    key: path ? targetId : null,
    active,
    fetchEvents,
    definitions: CHAT_PROGRESS_STEP_DEFINITIONS,
  });
  const partial = Boolean(path && dropped?.path === path && dropped.count > 0);
  return { ...snapshot, legacy: snapshot.legacy && !partial, partial };
}
