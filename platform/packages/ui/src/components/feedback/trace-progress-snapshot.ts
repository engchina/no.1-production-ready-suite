import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useTraceDetailsLabels } from "../data/trace-labels-provider";
import type { ChatProgressStep } from "./chat-progress";
import {
  chatProgressHasSpans,
  chatProgressTerminalOf,
  chatProgressTraceIdOf,
  labelChatProgressSteps,
  parseChatProgressEvents,
  reduceChatProgressEvents,
  type ChatProgressEvent,
  type ChatProgressStepDefinitions,
  type ChatProgressTerminalStatus,
} from "./chat-progress-events";

/**
 * 処理の経過（trace の投影。#3226 の案 A）を、共通の span の表の snapshot から読む hook（3 製品共通）。
 *
 * span の表は span ごとの最新の状態を持ち、全体の番号（`seq`）を持たないので、配信は snapshot の読み直しにする。
 * 実行中（`active`）は `intervalMs` ごとに読み直し、終わったら 1 回だけ読む。製品は自分の記録の判定を通した
 * API（例 `GET /api/runs/{run_id}/trace`）を `fetchEvents` で渡す（span の段のイベントの一覧を返す）。
 * 一覧の保持（処理中は出した段を消さない・状態を戻さない）は `ChatProgress` が持つ。
 */
export interface TraceProgressSnapshotOptions {
  /** 対象（回答・ジョブ・Run）。変わったら読み直す。null なら読まない。 */
  key: string | null;
  /** 実行中か（読み直しを続ける）。 */
  active: boolean;
  /** snapshot を読む（span の段のイベントの一覧。未検証の入力として検証する）。 */
  fetchEvents: (signal: AbortSignal) => Promise<unknown>;
  definitions: ChatProgressStepDefinitions;
  intervalMs?: number;
}

export interface TraceProgressSnapshot {
  events: ChatProgressEvent[];
  steps: ChatProgressStep[];
  traceId: string | undefined;
  terminal: ChatProgressTerminalStatus | null;
  /** 読めた記録に span が無い（古い形式・記録なし）。 */
  legacy: boolean;
  /** 最後の読み出しが失敗した（前の内容は残す）。 */
  failed: boolean;
  loaded: boolean;
  refresh: () => void;
}

export const TRACE_SNAPSHOT_INTERVAL_MS = 1000;

export function useTraceProgressSnapshot({
  key,
  active,
  fetchEvents,
  definitions,
  intervalMs = TRACE_SNAPSHOT_INTERVAL_MS,
}: TraceProgressSnapshotOptions): TraceProgressSnapshot {
  const traceLabels = useTraceDetailsLabels();
  const [state, setState] = useState<{ key: string | null; events: ChatProgressEvent[]; failed: boolean; loaded: boolean }>({
    key,
    events: [],
    failed: false,
    loaded: false,
  });
  const fetchRef = useRef(fetchEvents);
  useEffect(() => {
    fetchRef.current = fetchEvents;
  });
  const [generation, setGeneration] = useState(0);
  const refresh = useCallback(() => setGeneration((value) => value + 1), []);

  useEffect(() => {
    if (key === null) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const load = async () => {
      try {
        const raw = await fetchRef.current(controller.signal);
        if (controller.signal.aborted) return;
        setState({ key, events: parseChatProgressEvents(raw), failed: false, loaded: true });
      } catch {
        if (controller.signal.aborted) return;
        setState((before) => (before.key === key ? { ...before, failed: true } : { key, events: [], failed: true, loaded: false }));
      }
      if (active && !controller.signal.aborted) timer = setTimeout(() => void load(), intervalMs);
    };
    void load();
    return () => {
      controller.abort();
      if (timer) clearTimeout(timer);
    };
  }, [key, active, intervalMs, generation]);

  const events = useMemo(() => (state.key === key ? state.events : []), [state, key]);
  const steps = useMemo(() => {
    const reduced = reduceChatProgressEvents(null, { key, events, since: 0 });
    return labelChatProgressSteps(reduced.steps, definitions, { traceLabels });
  }, [events, key, definitions, traceLabels]);
  const loaded = state.key === key && state.loaded;
  return {
    events,
    steps,
    traceId: chatProgressTraceIdOf(events),
    terminal: chatProgressTerminalOf(events, key),
    legacy: loaded && events.length > 0 && !chatProgressHasSpans(events),
    failed: state.key === key && state.failed,
    loaded,
    refresh,
  };
}
