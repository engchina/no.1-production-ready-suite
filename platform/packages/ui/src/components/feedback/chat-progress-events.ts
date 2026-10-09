import type { ChatProgressStep, ChatProgressStepStatus } from "./chat-progress";
import { CHAT_PROGRESS_STATUS_RANK } from "./chat-progress-steps";

/**
 * チャットの処理の段階のイベント（3 製品共通の契約。#1359）。
 *
 * 正本は `platform/contracts/chat-progress/chat-progress-events.json`（backend の `pr_backend_core.chat_progress`
 * から生成する）。段階の一覧の snapshot を置き換えるのではなく、追記型のイベントを番号（`seq`）の順に積む。
 *
 * - 段階のイベント（`type: "step"`）は、その段階の今の状態の全体（状態・時刻・補足・名前の値）を持つ。
 * - 終端のイベント（`type: "terminal"`）の後、同じ試行（`attempt`）の段階のイベントは来ない。
 * - `seq` は対象（`target_id`）ごとに 1 から連続する。試行が増えたら段階の一覧を作り直す。
 *
 * 段階の名前は契約に入れない。製品の段階の定義（`ChatProgressStepDefinitions`。`kind` → i18n の名前）で付ける。
 */

export const CHAT_PROGRESS_SCHEMA_VERSION = 1;
/** SSE のイベントの名前（`event:`）。 */
export const CHAT_PROGRESS_SSE_EVENT = "chat_progress";
/** イベントの無い間の heartbeat（`event: heartbeat`、`data: {"last_seq": N}`）。 */
export const CHAT_PROGRESS_SSE_HEARTBEAT_EVENT = "heartbeat";

export const CHAT_PROGRESS_STEP_STATUSES: readonly ChatProgressStepStatus[] = [
  "pending",
  "running",
  "done",
  "failed",
  "skipped",
];
export const CHAT_PROGRESS_TERMINAL_STATUSES = ["done", "failed", "cancelled"] as const;
export type ChatProgressTerminalStatus = (typeof CHAT_PROGRESS_TERMINAL_STATUSES)[number];

/** 名前・補足を組み立てる値（ツール名・件数など）。 */
export type ChatProgressParamValue = string | number | boolean;
export type ChatProgressParams = Readonly<Record<string, ChatProgressParamValue>>;

interface ChatProgressEventBase {
  schema_version: 1;
  /** 対象ごとに 1 から連続して増える番号（SSE の `id:`）。 */
  seq: number;
  /** 対象（RAG の回答のメッセージ・NL2SQL のジョブ・Agent の Run）の id。 */
  target_id: string;
  /** 試行。引き継いだ実行で増え、増えたら段階の一覧を作り直す。 */
  attempt: number;
  /** ISO 8601。 */
  emitted_at: string;
}

export interface ChatProgressStepEvent extends ChatProgressEventBase {
  type: "step";
  step_id: string;
  /** 段階の定義の種類（名前の key）。省略時は `step_id`。 */
  kind?: string;
  status: ChatProgressStepStatus;
  started_at?: string;
  finished_at?: string;
  detail?: string;
  params?: ChatProgressParams;
}

export interface ChatProgressTerminalEvent extends ChatProgressEventBase {
  type: "terminal";
  status: ChatProgressTerminalStatus;
}

export type ChatProgressEvent = ChatProgressStepEvent | ChatProgressTerminalEvent;

/** polling の応答（`since` より後のイベント）。 */
export interface ChatProgressPage {
  target_id: string;
  attempt: number;
  events: ChatProgressEvent[];
  /** 記録の最後の番号。 */
  last_seq: number;
  /** 今の試行が終わったか。 */
  terminal: boolean;
}

// ---------------------------------------------------------------------------
// 入力の検証（SSE・API の応答は未検証の入力）
// ---------------------------------------------------------------------------

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
}

function text(value: unknown): string | undefined {
  return typeof value === "string" && value ? value : undefined;
}

function positiveInt(value: unknown, min: number): number | null {
  return typeof value === "number" && Number.isInteger(value) && value >= min ? value : null;
}

function params(value: unknown): ChatProgressParams | undefined {
  const source = record(value);
  if (!source) return undefined;
  const entries = Object.entries(source).filter(
    (entry): entry is [string, ChatProgressParamValue] =>
      typeof entry[1] === "string" || typeof entry[1] === "number" || typeof entry[1] === "boolean"
  );
  return entries.length > 0 ? Object.fromEntries(entries) : undefined;
}

/** 1 件のイベントを検証する。形の違うものは null。 */
export function parseChatProgressEvent(value: unknown): ChatProgressEvent | null {
  const source = record(value);
  if (!source) return null;
  const seq = positiveInt(source.seq, 1);
  const attempt = positiveInt(source.attempt ?? 0, 0);
  const targetId = text(source.target_id);
  if (seq === null || attempt === null || !targetId) return null;
  const base = {
    schema_version: 1 as const,
    seq,
    target_id: targetId,
    attempt,
    emitted_at: text(source.emitted_at) ?? "",
  };
  if (source.type === "terminal") {
    const status = CHAT_PROGRESS_TERMINAL_STATUSES.find((candidate) => candidate === source.status);
    return status ? { ...base, type: "terminal", status } : null;
  }
  if (source.type !== "step") return null;
  const stepId = text(source.step_id);
  const status = CHAT_PROGRESS_STEP_STATUSES.find((candidate) => candidate === source.status);
  if (!stepId || !status) return null;
  const event: ChatProgressStepEvent = { ...base, type: "step", step_id: stepId, status };
  const kind = text(source.kind);
  if (kind) event.kind = kind;
  const startedAt = text(source.started_at);
  if (startedAt) event.started_at = startedAt;
  const finishedAt = text(source.finished_at);
  if (finishedAt) event.finished_at = finishedAt;
  const detail = text(source.detail);
  if (detail) event.detail = detail;
  const values = params(source.params);
  if (values) event.params = values;
  return event;
}

/** イベントの一覧を検証する（形の違う要素は捨てる）。 */
export function parseChatProgressEvents(value: unknown): ChatProgressEvent[] {
  if (!Array.isArray(value)) return [];
  return value.map(parseChatProgressEvent).filter((event): event is ChatProgressEvent => event !== null);
}

/** polling の応答を検証する。形の違うものは null。 */
export function parseChatProgressPage(value: unknown): ChatProgressPage | null {
  const source = record(value);
  if (!source) return null;
  const targetId = text(source.target_id);
  const lastSeq = positiveInt(source.last_seq, 0);
  const attempt = positiveInt(source.attempt ?? 0, 0);
  if (!targetId || lastSeq === null || attempt === null) return null;
  return {
    target_id: targetId,
    attempt,
    events: parseChatProgressEvents(source.events),
    last_seq: lastSeq,
    terminal: source.terminal === true,
  };
}

// ---------------------------------------------------------------------------
// 組み立て（イベント → 段階の一覧）。backend の `fold_chat_progress_events` と同じ規則。
// ---------------------------------------------------------------------------

/** 組み立てた 1 つの段階（名前を付ける前）。 */
export interface ChatProgressEventStep {
  id: string;
  kind: string;
  status: ChatProgressStepStatus;
  startedAt?: string;
  finishedAt?: string;
  detail?: string;
  params?: ChatProgressParams;
}

export interface ChatProgressEventsState {
  /** 対象（`target_id`）。変わったら作り直す。 */
  key: string | null;
  /** 今の試行。 */
  attempt: number;
  /** 続けて適用した最後の番号（取り直しの `since`・SSE の再開の位置）。 */
  lastSeq: number;
  /** 今の試行の終端。 */
  terminal: ChatProgressTerminalStatus | null;
  /** 段階（最初に出た順）。 */
  steps: ChatProgressEventStep[];
  /** 番号が飛んで、まだ適用していないイベント（前の番号が届いたら適用する）。 */
  pending: ChatProgressEvent[];
}

export interface ChatProgressEventsInput {
  key: string | null;
  events: readonly ChatProgressEvent[];
  /**
   * この一覧が `since` より後のイベントをすべて含む（polling の応答）。番号が飛んでいても、それより前は
   * 記録に無いとみなして進める（取り直しで埋まらない飛びで止まらない）。
   */
  since?: number;
}

export function createChatProgressEventsState(key: string | null): ChatProgressEventsState {
  return { key, attempt: 0, lastSeq: 0, terminal: null, steps: [], pending: [] };
}

function applyEvent(state: ChatProgressEventsState, event: ChatProgressEvent): ChatProgressEventsState {
  const next: ChatProgressEventsState = { ...state, lastSeq: event.seq };
  if (event.attempt < state.attempt) return next;
  if (event.attempt > state.attempt) {
    next.attempt = event.attempt;
    next.terminal = null;
    next.steps = [];
  }
  if (event.type === "terminal") return { ...next, terminal: event.status };
  if (next.terminal !== null) return next;
  const index = next.steps.findIndex((step) => step.id === event.step_id);
  const before = index >= 0 ? next.steps[index] : undefined;
  // 状態は進む方へだけ変える（戻るイベントは古い。#1358）。
  if (before && CHAT_PROGRESS_STATUS_RANK[event.status] < CHAT_PROGRESS_STATUS_RANK[before.status]) return next;
  const step: ChatProgressEventStep = {
    id: event.step_id,
    kind: event.kind ?? before?.kind ?? event.step_id,
    status: event.status,
  };
  const startedAt = event.started_at ?? before?.startedAt;
  if (startedAt) step.startedAt = startedAt;
  if (event.finished_at) step.finishedAt = event.finished_at;
  if (event.detail) step.detail = event.detail;
  if (event.params) step.params = event.params;
  const steps = [...next.steps];
  if (index >= 0) steps[index] = step;
  else steps.push(step);
  return { ...next, steps };
}

/**
 * イベントを段階の一覧の状態に積む（3 製品共通の規則。純粋な関数）。
 *
 * - 対象（`key`）が変わったら作り直す。別の対象のイベントは捨てる。
 * - `seq` の順に、続けて（前の番号の次から）適用する。適用済みの番号以下（古い・重複）は捨て、番号が飛んだ
 *   イベントは前の番号が届くまで取っておく（`pending`。届かなければ取り直しの `since` で埋める）。
 * - 試行（`attempt`）が増えたら一覧を作り直し、古い試行のイベントは捨てる。
 * - 段階は `step_id` で結び、最初に出た順に並べる。状態は進む方へだけ変える。終端の後の段階のイベントは捨てる。
 *
 * 変わらなければ `state` をそのまま返す。
 */
export function reduceChatProgressEvents(
  state: ChatProgressEventsState | null,
  { key, events, since }: ChatProgressEventsInput
): ChatProgressEventsState {
  let current = state === null || state.key !== key ? createChatProgressEventsState(key) : state;
  if (key === null) return current;
  const incoming = events.filter((event) => event.target_id === key);
  if (incoming.length === 0 && (since === undefined || current.pending.length === 0)) return current;
  const bySeq = new Map<number, ChatProgressEvent>();
  for (const event of [...current.pending, ...incoming]) {
    if (event.seq > current.lastSeq && !bySeq.has(event.seq)) bySeq.set(event.seq, event);
  }
  const ordered = [...bySeq.values()].sort((a, b) => a.seq - b.seq);
  let next = current;
  const held: ChatProgressEvent[] = [];
  for (const event of ordered) {
    const contiguous = event.seq === next.lastSeq + 1;
    // polling の応答は `since` の後をすべて含む（その前の飛びは記録に無い）。
    const covered = since !== undefined && since >= next.lastSeq && incoming.includes(event);
    if (contiguous || (covered && held.length === 0)) next = applyEvent(next, event);
    else held.push(event);
  }
  const samePending =
    held.length === current.pending.length && held.every((event, index) => event === current.pending[index]);
  if (next === current && samePending) return current;
  current = { ...next, pending: samePending ? current.pending : held };
  return current;
}

// ---------------------------------------------------------------------------
// 段階の定義（製品が持つ: id・順序・i18n の名前）
// ---------------------------------------------------------------------------

/** 名前の状態（実行中「〜しています」・完了「〜しました」・失敗「〜できませんでした」・未実行は名詞）。 */
export type ChatProgressLabelState = "running" | "done" | "failed" | "idle";

export function chatProgressLabelState(status: ChatProgressStepStatus): ChatProgressLabelState {
  return status === "running" || status === "done" || status === "failed" ? status : "idle";
}

/** 1 つの種類（`kind`）の段階の定義。 */
export interface ChatProgressStepDefinition {
  /** 状態と値（`params`）から名前を作る（製品の i18n）。 */
  label: (status: ChatProgressStepStatus, params: ChatProgressParams) => string;
  /** 状態と値から補足を作る。省略時・undefined はイベントの `detail`。 */
  detail?: (status: ChatProgressStepStatus, params: ChatProgressParams) => string | undefined;
}

/** 製品の段階の定義（`kind` → 定義）。並びは backend の記録の順（最初に出た順）。 */
export type ChatProgressStepDefinitions = Readonly<Record<string, ChatProgressStepDefinition>>;

const EMPTY_PARAMS: ChatProgressParams = Object.freeze({});

/** 組み立てた段階に名前を付ける（未知の種類は id をそのまま出す）。 */
export function labelChatProgressSteps(
  steps: readonly ChatProgressEventStep[],
  definitions: ChatProgressStepDefinitions
): ChatProgressStep[] {
  return steps.map((step) => {
    const definition = Object.prototype.hasOwnProperty.call(definitions, step.kind) ? definitions[step.kind] : undefined;
    const values = step.params ?? EMPTY_PARAMS;
    const result: ChatProgressStep = {
      id: step.id,
      label: definition ? definition.label(step.status, values) : step.id,
      status: step.status,
    };
    if (step.startedAt) result.startedAt = step.startedAt;
    if (step.finishedAt) result.finishedAt = step.finishedAt;
    const detail = definition?.detail?.(step.status, values) ?? step.detail;
    if (detail) result.detail = detail;
    return result;
  });
}

/** 保存済みのイベント（完了した回答など）を、名前を付けた段階の一覧にする。 */
export function chatProgressStepsFromEvents(
  events: readonly ChatProgressEvent[],
  definitions: ChatProgressStepDefinitions,
  key: string | null = events[0]?.target_id ?? null
): ChatProgressStep[] {
  const state = reduceChatProgressEvents(null, { key, events, since: 0 });
  return labelChatProgressSteps(state.steps, definitions);
}

/** 保存済みのイベントの終端（無ければ null）。 */
export function chatProgressTerminalOf(
  events: readonly ChatProgressEvent[],
  key: string | null = events[0]?.target_id ?? null
): ChatProgressTerminalStatus | null {
  return reduceChatProgressEvents(null, { key, events, since: 0 }).terminal;
}
