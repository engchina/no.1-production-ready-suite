import { useCallback, useEffect, useRef, useState } from "react";

import { chatProgressHasSpans, useTraceProgressSnapshot, type TraceProgressSnapshot } from "@production-ready/ui";

import { apiGet } from "@/lib/api";
import { API_TIMEOUT_MS } from "@/lib/requestPolicy";
import { CHAT_PROGRESS_STEP_DEFINITIONS, parseTraceProgressResponse } from "./chatProgress";

export interface Nl2SqlTraceProgress extends TraceProgressSnapshot {
  /** 書けなかった span がある（処理の経過に「一部の処理だけを記録しています。」を出す）。 */
  partial: boolean;
}

/**
 * 終わった記録を読み直す間隔と回数。span は worker の書き手が間隔（既定 0.5 秒）でまとめて書くので、ジョブが終わった
 * 直後の読み出しには、最後の span（と、すぐ終わったジョブではすべての span）がまだ入っていないことがある。
 */
export const TRACE_SETTLE_DELAY_MS = 1500;
export const TRACE_SETTLE_ATTEMPTS = 2;

/**
 * 記録（ジョブ・実行履歴の行・DB 管理チャットのターン）の処理の経過（#3226 の案 A）。
 *
 * span は共通の span の表にあり、記録ごとの API（`path`。記録の判定を通した後に、その記録の trace id で読む）が
 * span の段のイベントの一覧（snapshot）を返す。実行中（`active`）は共有の `useTraceProgressSnapshot` が読み直す。
 * `targetId` はイベントの `target_id`（記録の ID）。`path` が null なら読まない。
 *
 * 書き手は span を後からまとめて書くので、終わった記録は書き終わるのを待って読み直す: 処理中から終わったときは
 * 1 回、終わった記録で span がまだ無いときは {@link TRACE_SETTLE_ATTEMPTS} 回まで。読み直しを終えるまでは古い形式
 * （`legacy`）と出さない。一部だけの記録（`partial`）は古い形式より優先する（span が 1 つも書けなかった実行を古い
 * 形式と出さない）。
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
  const { refresh, loaded, events } = snapshot;
  const hasSpans = chatProgressHasSpans(events);

  // 終わった記録の読み直し（対象ごとに数える）。
  const [settle, setSettle] = useState<{ path: string | null; attempts: number; finished: boolean }>({
    path,
    attempts: 0,
    finished: false,
  });
  const current = settle.path === path ? settle : { path, attempts: 0, finished: false };
  const wasActive = useRef(active);
  useEffect(() => {
    const finished = wasActive.current && !active;
    wasActive.current = active;
    if (finished) setSettle((before) => ({ ...(before.path === path ? before : { path, attempts: 0 }), finished: true }));
  }, [active, path]);
  const settleNeeded =
    Boolean(path) &&
    !active &&
    loaded &&
    current.attempts < TRACE_SETTLE_ATTEMPTS &&
    (current.finished || !hasSpans);
  useEffect(() => {
    if (!settleNeeded) return;
    const timer = setTimeout(() => {
      setSettle((before) => {
        const base = before.path === path ? before : { path, attempts: 0, finished: false };
        // 処理中から終わったときの読み直しは 1 回でよい（span があれば終える）。
        return { path, attempts: base.attempts + 1, finished: false };
      });
      refresh();
    }, TRACE_SETTLE_DELAY_MS);
    return () => clearTimeout(timer);
  }, [settleNeeded, current.attempts, path, refresh]);

  const partial = Boolean(path && dropped?.path === path && dropped.count > 0);
  const settled = active || hasSpans || current.attempts >= TRACE_SETTLE_ATTEMPTS;
  return { ...snapshot, legacy: snapshot.legacy && !partial && settled, partial };
}
