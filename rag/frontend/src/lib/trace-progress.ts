/**
 * 処理の経過を共通の span の表の snapshot から読む（#3226 の案 A）。3 製品共通の `useTraceProgressSnapshot` に、RAG の
 * 工程の定義と、書けなかった span の数（`dropped_spans`。1 以上なら「一部の処理だけを記録しています。」）を足す。
 *
 * snapshot の API は記録ごと（チャットの回答・実行履歴・RAG 検索の実行）にあり、画面は記録の判定を通した API だけを
 * 読む（任意の trace id を渡さない）。実行中（`active`）は読み直し、終わったら 1 回だけ読む。
 */
import { useState } from "react";
import { useTraceProgressSnapshot, type TraceProgressSnapshot } from "@production-ready/ui";

import type { TraceSnapshotData } from "./api";
import { CHAT_PROGRESS_DEFINITIONS } from "./chat-progress";

export interface RagTraceProgress extends TraceProgressSnapshot {
  /** 書けなかった span があった（一部の処理だけを記録している）。 */
  partial: boolean;
}

export function useRagTraceProgress({
  key,
  active,
  fetchSnapshot,
  intervalMs,
}: {
  /** 対象（回答のメッセージ・実行の id。snapshot のイベントの `target_id` と同じ値）。null なら読まない。 */
  key: string | null;
  active: boolean;
  fetchSnapshot: (signal: AbortSignal) => Promise<TraceSnapshotData>;
  intervalMs?: number;
}): RagTraceProgress {
  const [dropped, setDropped] = useState<{ key: string | null; count: number }>({ key: null, count: 0 });
  const snapshot = useTraceProgressSnapshot({
    key,
    active,
    definitions: CHAT_PROGRESS_DEFINITIONS,
    intervalMs,
    fetchEvents: async (signal) => {
      const data = await fetchSnapshot(signal);
      const count = typeof data.dropped_spans === "number" && data.dropped_spans > 0 ? data.dropped_spans : 0;
      setDropped((before) => (before.key === key && before.count === count ? before : { key, count }));
      return data.events;
    },
  });
  return { ...snapshot, partial: dropped.key === key && dropped.count > 0 };
}
