/**
 * チャットの処理の段階のイベント（3 製品共通の契約。#1359）を spec の mock で作る。
 *
 * backend（`app/rag/chat_progress.py` の `ChatProgressTracker`）と同じく、最初に 5 段階を待機中で出し、状態が
 * 変わった段階だけを番号（`seq`。対象ごとに 1 から連続）つきで記録する。名前（文言）は入れない（画面が i18n で付ける）。
 */

export const CHAT_STEP_IDS = ["rewrite_query", "retrieve", "rerank", "generate_answer", "check_guardrail"] as const;
export type ChatStepId = (typeof CHAT_STEP_IDS)[number];
export type ChatStepStatus = "pending" | "running" | "done" | "failed" | "skipped";
export type ChatStepParams = Record<string, string | number | boolean>;

export interface ChatProgressStepEventJson {
  schema_version: 1;
  seq: number;
  target_id: string;
  attempt: number;
  emitted_at: string;
  type: "step";
  step_id: string;
  status: ChatStepStatus;
  started_at?: string;
  finished_at?: string;
  params?: ChatStepParams;
}

export interface ChatProgressTerminalEventJson {
  schema_version: 1;
  seq: number;
  target_id: string;
  attempt: number;
  emitted_at: string;
  type: "terminal";
  status: "done" | "failed" | "cancelled";
}

export type ChatProgressEventJson = ChatProgressStepEventJson | ChatProgressTerminalEventJson;

interface StepState {
  status: ChatStepStatus;
  started_at?: string;
  finished_at?: string;
  params?: ChatStepParams;
}

/** 1 つの回答（`targetId`）の段階のイベントを記録する。`now` は時刻の文字列（配信の時刻に置き換える印でもよい）。 */
export function chatProgressRecorder(targetId: string, now: () => string = () => new Date().toISOString()) {
  let seq = 0;
  const steps = new Map<string, StepState>();
  const base = () => ({ schema_version: 1 as const, seq: ++seq, target_id: targetId, attempt: 0, emitted_at: now() });

  function record(stepId: string, next: StepState): ChatProgressStepEventJson {
    steps.set(stepId, next);
    return { ...base(), type: "step", step_id: stepId, ...next };
  }

  return {
    get seq() {
      return seq;
    },
    /** 5 段階を待機中で出す。 */
    declare(): ChatProgressEventJson[] {
      return CHAT_STEP_IDS.filter((id) => !steps.has(id)).map((id) => record(id, { status: "pending" }));
    },
    /** 段階の状態を変える（変わった段階だけを記録する）。`params` は段階ごとの値（根拠の件数など）。 */
    set(
      statuses: Partial<Record<ChatStepId, ChatStepStatus>>,
      params: Partial<Record<ChatStepId, ChatStepParams>> = {}
    ): ChatProgressEventJson[] {
      const events: ChatProgressEventJson[] = [];
      for (const id of CHAT_STEP_IDS) {
        const status = statuses[id];
        if (status === undefined) continue;
        const before = steps.get(id) ?? { status: "pending" as const };
        const value = params[id] ?? before.params;
        if (before.status === status && value === before.params) continue;
        const at = now();
        events.push(
          record(id, {
            status,
            ...(status === "pending" || (status === "skipped" && !before.started_at)
              ? {}
              : { started_at: before.started_at ?? at }),
            ...(status === "done" || status === "failed" || (status === "skipped" && before.started_at)
              ? { finished_at: at }
              : {}),
            ...(value ? { params: value } : {}),
          })
        );
      }
      return events;
    },
    /** 終端を記録する。 */
    terminal(status: "done" | "failed" | "cancelled"): ChatProgressEventJson[] {
      return [{ ...base(), type: "terminal", status }];
    },
  };
}

/** 保存済みの回答の段階（会話の取得の `progress`）。状態を順に記録したイベントの一覧。 */
export function savedChatProgress(
  targetId: string,
  statuses: Partial<Record<ChatStepId, ChatStepStatus>>,
  options: { terminal?: "done" | "failed" | "cancelled"; params?: Partial<Record<ChatStepId, ChatStepParams>> } = {}
): ChatProgressEventJson[] {
  const recorder = chatProgressRecorder(targetId);
  return [
    ...recorder.declare(),
    ...recorder.set(statuses, options.params),
    ...(options.terminal ? recorder.terminal(options.terminal) : []),
  ];
}
