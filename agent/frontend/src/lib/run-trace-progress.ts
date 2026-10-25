import { useCallback, useMemo, useState } from "react";
import { useTraceProgressSnapshot, type TraceProgressSnapshot } from "@production-ready/ui";

import { agentApi, type RunState } from "@/lib/api";
import { agentProgressDefinitions, runProgressIsLegacy } from "@/lib/run-history";

/**
 * Run の処理の経過（共通の span の表の snapshot。#3226 の案 A）を読む hook。チャットと実行の詳細で同じ記録・名前にする。
 *
 * - `GET /api/runs/{id}/trace` を共通の `useTraceProgressSnapshot` で読み、実行中（`active`）は読み直す。最終の状態は
 *   Run の状態（正本）で、backend が表で実行中のままの span を Run の終端に合わせて閉じる。
 * - 1 回の読み出しは `TRACE_FETCH_TIMEOUT_MS` で打ち切る（応答しない読み出しで読み直しを止めない）。失敗したら前の内容を
 *   残し、`failed` の間は画面が「接続を確認しています」を出す（#1160）。
 * - 古い形式の記録（`runProgressIsLegacy`）は読まない。表に書けなかった span があれば `partial`（「一部の処理だけを
 *   記録しています。」）。
 */
export const TRACE_FETCH_TIMEOUT_MS = 8_000;

export interface RunTraceProgress extends TraceProgressSnapshot {
  /** 古い形式の記録（処理の経過を出せない）。 */
  legacy: boolean;
  /** 表に書けなかった span がある（一部だけの記録）。 */
  partial: boolean;
}

function withTimeout(signal: AbortSignal, timeoutMs: number): { signal: AbortSignal; done: () => void } {
  const controller = new AbortController();
  const abort = () => controller.abort();
  const timer = setTimeout(abort, timeoutMs);
  if (signal.aborted) abort();
  else signal.addEventListener("abort", abort, { once: true });
  return {
    signal: controller.signal,
    done: () => {
      clearTimeout(timer);
      signal.removeEventListener("abort", abort);
    },
  };
}

export function useRunTraceProgress(
  run: Pick<RunState, "id" | "status" | "steps" | "trace_store" | "trace_id" | "trace_dropped_spans">,
  { active, timeoutMs = TRACE_FETCH_TIMEOUT_MS }: { active: boolean; timeoutMs?: number }
): RunTraceProgress {
  const legacy = runProgressIsLegacy(run);
  const runId = run.id;
  const [dropped, setDropped] = useState<{ key: string; count: number } | null>(null);
  const fetchEvents = useCallback(
    async (signal: AbortSignal) => {
      const limited = withTimeout(signal, timeoutMs);
      try {
        const data = await agentApi.getRunTrace(runId, { signal: limited.signal });
        setDropped({ key: runId, count: data.dropped_spans ?? 0 });
        return data.events;
      } finally {
        limited.done();
      }
    },
    [runId, timeoutMs]
  );
  // ツールの span の詳細に、Run の step のデータの範囲の記録・取り直した結果を足す（チャットと実行の詳細で同じ。#2294）。
  const definitions = useMemo(() => agentProgressDefinitions(run.steps), [run.steps]);
  // 終わった Run の最後の読み出しが失敗した・まだ終端を含まないときは、終端が届くまで読み直す（共通の hook は実行中で
  // なければ 1 回だけ読む）。trace の無い Run（開始の前に終わった）は終端の無い空の snapshot なので読み直さない。
  const [catchingUp, setCatchingUp] = useState(false);
  const snapshot = useTraceProgressSnapshot({
    key: legacy ? null : runId,
    active: !legacy && (active || catchingUp),
    fetchEvents,
    definitions,
  });
  const behind = !active && !legacy && Boolean(run.trace_id) && snapshot.terminal === null;
  // 前の描画の結果から決める状態なので、effect を待たずに描画の中で合わせる（React の「前の描画の情報を保存する」）。
  if (catchingUp !== behind) setCatchingUp(behind);
  const droppedSpans = Math.max(dropped?.key === runId ? dropped.count : 0, run.trace_dropped_spans ?? 0);
  return { ...snapshot, legacy, partial: !legacy && droppedSpans > 0 };
}
