import {
  chatProgressStepsFromEvents,
  chatProgressTimelineStep,
  runStepDetailsJson,
  type ChatProgressStep,
  type RunStepDetailCode,
  type RunStepDetailField,
  type RunStepDetails,
  type RunExecutorFilter as CommonRunExecutorFilter,
  type RunDetailLabelsInput,
  type RunHistoryLabelsInput,
  type RunStatus as CommonRunStatus,
  type RunStatusFilter as CommonRunStatusFilter,
  type RunTimelineStep,
  type RunTimelineStepStatus,
} from "@production-ready/ui";

import type { RunExecutorFilter, RunListKind, RunListKindQuery, RunListStatus, RunState, RunStep } from "@/lib/api";
import { AGENT_CHAT_PROGRESS_STEPS } from "@/lib/chat-progress";
import { dataScopeNotes } from "@/lib/data-scope";
import { t } from "@/lib/i18n";
import { runStatusView } from "@/lib/status-labels";

/*
 * Agent の実行履歴を共通の実行履歴の型（UX 契約 run-history.md。#1955）に写す。
 * 状態は共通の 7 つ（`RunStatus`）、文言は Agent に固有の言い方（承認待ち・確認待ち）を `statusLabel` で上書きする。
 */

/** Agent の Run の状態 → 共通の状態（run-history.md「状態の写し方」）。 */
export function commonRunStatus(status: RunState["status"]): CommonRunStatus {
  switch (status) {
    case "queued":
      return "queued";
    case "running":
      return "running";
    case "waiting_approval":
    case "waiting_confirmation":
      return "waiting";
    case "completed":
      return "succeeded";
    case "failed":
      return "failed";
    case "cancelled":
      return "cancelled";
  }
}

/**
 * 状態の文言の上書き（Agent に固有の言い方だけ。waiting_approval →「承認待ち」、waiting_confirmation →「確認待ち」）。
 * それ以外は共通の文言（待機中・実行中・完了・失敗・停止）のまま（3 製品で同じ語。run-history.md「状態の写し方」）。
 */
export function runStatusLabel(status: RunState["status"]): string | undefined {
  return status === "waiting_approval" || status === "waiting_confirmation" ? runStatusView(status).label : undefined;
}

/** 一覧の状態の絞り込みに出す共通の状態（Agent が写せるものだけ。ブロックは無い）。 */
export const AGENT_RUN_STATUS_OPTIONS: readonly CommonRunStatus[] = [
  "queued",
  "running",
  "waiting",
  "succeeded",
  "failed",
  "cancelled",
];

/** 共通の状態の絞り込み → backend の `status`（`waiting` は承認待ち・確認待ち）。 */
export function backendRunStatus(filter: CommonRunStatusFilter): RunListStatus | undefined {
  switch (filter) {
    case "all":
      return undefined;
    case "succeeded":
      return "completed";
    case "blocked":
      // Agent には「ブロック」が無い（選択肢にも出さない）。
      return undefined;
    default:
      return filter;
  }
}

/** 共通の実行者の絞り込み（self）→ backend の値（mine）。 */
export function backendRunExecutor(filter: CommonRunExecutorFilter): Exclude<RunExecutorFilter, "all"> | undefined {
  if (filter === "all") return undefined;
  return filter === "self" ? "mine" : "others";
}

/** 種類（きっかけ）の絞り込みの値（一つずつ選ぶもの）。 */
export const AGENT_RUN_KINDS: readonly RunListKind[] = ["user", "automation", "mcp", "replay", "evaluation"];

/**
 * 種類の絞り込みの既定。品質評価の Run（評価のケースごとの Run）は利用者の実行に混ざると埋もれさせるので、
 * 既定では隠し、「すべて」か「品質評価」を選んだときに出す（#1983）。
 */
export const DEFAULT_AGENT_RUN_KIND = "not_evaluation";

export type AgentRunKindFilter = "all" | typeof DEFAULT_AGENT_RUN_KIND | RunListKind;

/** 画面の種類の絞り込み → `GET /api/runs` の `kind`（「すべて」は送らない）。 */
export function backendRunKind(filter: AgentRunKindFilter): RunListKindQuery | undefined {
  return filter === "all" ? undefined : filter;
}

/** 一覧・詳細で使う共通の部品の文言（Agent の i18n）。 */
export function agentRunHistoryLabels(): RunHistoryLabelsInput {
  return {
    table: t("run.history"),
    columns: {
      title: t("run.column.question"),
      status: t("common.status"),
      kind: t("run.column.kind"),
      executor: t("run.column.executor"),
      startedAt: t("run.column.startedAt"),
      duration: t("run.column.duration"),
      usage: t("run.column.usage"),
      actions: t("run.actions"),
    },
    self: t("run.self"),
    openDetail: (title) => t("run.openDetail", { title }),
    rowActions: (title) => t("common.entityActions", { name: title }),
  };
}

/** 詳細の共通の部品（RunSummary・RunTimeline）の文言（Agent の i18n）。 */
export function agentRunDetailLabels(): RunDetailLabelsInput {
  return {
    summary: t("run.section.summary"),
    question: t("run.section.question"),
    answer: t("run.section.answer"),
    timeline: t("run.section.timeline"),
    technical: t("run.section.technical"),
    facts: {
      startedAt: t("run.fact.startedAt"),
      finishedAt: t("run.fact.finishedAt"),
      duration: t("run.fact.duration"),
      kind: t("run.fact.kind"),
      executor: t("run.executor"),
      owner: t("run.conversationOwner"),
      model: t("run.fact.model"),
      tokens: t("run.fact.tokens"),
      cost: t("run.fact.cost"),
      runId: t("run.fact.runId"),
    },
    self: t("run.self"),
    timelineSummary: (count, duration) => t("run.timelineSummary", { count, duration }),
    stepStatus: {
      pending: t("run.stepStatus.pending"),
      running: t("run.stepStatus.running"),
      waiting: t("run.stepStatus.waiting"),
      done: t("run.stepStatus.done"),
      failed: t("run.stepStatus.failed"),
      skipped: t("run.stepStatus.skipped"),
    },
  };
}

/** 終わった Run の終了日時（終わりの Event の時刻。backend の `run_finished_at` と同じ判定）。 */
export function runFinishedAt(run: RunState): string | null {
  if (!["completed", "failed", "cancelled"].includes(run.status)) return null;
  const terminal = [...run.events]
    .reverse()
    .find((event) => event.type === "run.completed" || event.type === "runtime.failed" || event.type === "run.cancelled");
  return terminal?.created_at ?? run.updated_at;
}

export function runDurationMs(run: RunState): number | null {
  const finished = runFinishedAt(run);
  if (!finished) return null;
  const ms = Date.parse(finished) - Date.parse(run.created_at);
  return Number.isFinite(ms) ? Math.max(0, ms) : null;
}

/** 失敗・取消の理由（最後の失敗の Event の文）。 */
export function runFailureMessage(run: RunState): string | null {
  if (run.status !== "failed") return null;
  const failed = [...run.events].reverse().find((event) => event.type.endsWith("failed"));
  return failed?.message || null;
}

function stepTimelineStatus(step: RunStep): RunTimelineStepStatus {
  switch (step.status) {
    case "completed":
      return "done";
    case "failed":
      return "failed";
    case "running":
      return "running";
    case "waiting_approval":
      return "waiting";
    case "cancelled":
      return "skipped";
    default:
      return "pending";
  }
}

/** Run に渡したツールの表示名（MCP の `title`。backend の metadata `tool_titles`）。 */
export function runToolTitles(run: Pick<RunState, "metadata">): Record<string, string> {
  const value = run.metadata?.tool_titles;
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return Object.fromEntries(
    Object.entries(value).filter((entry): entry is [string, string] => typeof entry[1] === "string" && entry[1] !== "")
  );
}

/** ツールの段（`run.steps`）の名前（チャットの処理の経過と同じ文言。表示名があれば表示名）。 */
function toolStepLabel(step: RunStep, status: RunTimelineStepStatus, titles: Record<string, string>): string {
  const tool = step.tool_call?.name ?? step.kind;
  const progressStatus = status === "waiting" ? "running" : status;
  const title = step.tool_call ? titles[step.tool_call.name] : undefined;
  return AGENT_CHAT_PROGRESS_STEPS.tool.label(progressStatus, title ? { tool, title } : { tool });
}

/**
 * 処理の経過の段（`RunTimeline`）。段の名前と状態はチャットの処理の経過（`progress_events` →
 * `AGENT_CHAT_PROGRESS_STEPS`）と同じにし、ツールの段には Run の step（引数・結果・エラー）を中身として結び付ける
 * （同じツールの n 回目の段 ↔ n 回目の step）。段の記録の無い古い Run は step だけで並べる。
 */
/**
 * 処理の経過のツールの段（`tool:<name>` / `tool:<name>#n`）と Run の step（引数・結果）を結ぶ（同じツールの n 回目の段
 * ↔ n 回目の step）。実行の詳細とチャットの処理の経過で同じ対応にする（#2294）。
 */
export function pairToolProgressSteps(
  progress: readonly Pick<ChatProgressStep, "id">[],
  steps: readonly RunStep[]
): Map<string, RunStep> {
  const toolSteps = steps.filter((step) => step.tool_call);
  const used = new Set<string>();
  const pairs = new Map<string, RunStep>();
  for (const item of progress) {
    if (!item.id.startsWith("tool:")) continue;
    const tool = item.id.slice("tool:".length).replace(/#\d+$/, "");
    const match = toolSteps.find((step) => step.tool_call?.name === tool && !used.has(step.id));
    if (!match) continue;
    used.add(match.id);
    pairs.set(item.id, match);
  }
  return pairs;
}

/**
 * ツールの段の詳細（ツールの内部名・データの範囲の記録・エラー・引数と結果の JSON。#2294）。チャットの処理の経過で
 * 段を開いて見る。段の名前は表示名（MCP の `title`）のことがあるので、調べる用に内部名を先頭に出す（#2324）。
 * 長い JSON は先頭だけにして省略の印を付ける。ツールの呼び出しでなければ undefined（段に開閉のボタンを付けない）。
 */
export function toolStepDetails(step: RunStep): RunStepDetails | undefined {
  const lines = dataScopeNotes(step.tool_call?.data_scope);
  const error = step.tool_result?.error;
  const args = step.tool_call?.arguments;
  const output = step.tool_result?.output;
  const code: RunStepDetailCode[] = [];
  let truncated = false;
  if (args && Object.keys(args).length > 0) {
    const json = runStepDetailsJson(args);
    truncated ||= json.truncated;
    code.push({ label: t("chat.progress.details.arguments"), language: "json", text: json.text });
  }
  if (output != null) {
    const json = runStepDetailsJson(output);
    truncated ||= json.truncated;
    code.push({ label: t("chat.progress.details.result"), language: "json", text: json.text });
  }
  const name = step.tool_call?.name;
  if (!name && !lines.length && !error && !code.length) return undefined;
  const details: RunStepDetails = {};
  const fields: RunStepDetailField[] = [];
  if (name) fields.push({ label: t("chat.progress.details.tool"), value: name, mono: true });
  if (error) fields.push({ label: t("chat.progress.details.error"), value: error });
  if (fields.length) details.fields = fields;
  if (lines.length) details.lines = lines;
  if (code.length) details.code = code;
  if (truncated) details.truncated = true;
  return details;
}

/** チャットの処理の経過の段に、ツールの段の詳細を足す（Run の step と結べた段だけ）。 */
export function withToolStepDetails(progress: ChatProgressStep[], steps: readonly RunStep[]): ChatProgressStep[] {
  const pairs = pairToolProgressSteps(progress, steps);
  if (pairs.size === 0) return progress;
  return progress.map((item) => {
    const runStep = pairs.get(item.id);
    const details = runStep ? toolStepDetails(runStep) : undefined;
    return details ? { ...item, details } : item;
  });
}

export function runTimelineSteps(
  run: RunState,
  detailsOf: (step: RunStep) => RunTimelineStep["children"]
): RunTimelineStep[] {
  const progress = run.progress_events?.length
    ? chatProgressStepsFromEvents(run.progress_events, AGENT_CHAT_PROGRESS_STEPS, run.id)
    : [];
  const pairs = pairToolProgressSteps(progress, run.steps);
  const used = new Set([...pairs.values()].map((step) => step.id));
  const titles = runToolTitles(run);
  const steps: RunTimelineStep[] = progress.map((item) => {
    const waiting =
      item.status === "running" &&
      (item.id.startsWith("approval") || item.id.startsWith("confirmation")) &&
      (run.status === "waiting_approval" || run.status === "waiting_confirmation");
    const status: RunTimelineStepStatus = waiting ? "waiting" : item.status;
    const runStep = pairs.get(item.id);
    // 段の写しはチャットの処理の経過と同じ共通の変換（補足・計測値・入れ子。#2265）に、Run の step の時刻と中身を足す。
    return {
      ...chatProgressTimelineStep(item),
      status,
      startedAt: item.startedAt ?? runStep?.started_at ?? null,
      finishedAt: item.finishedAt ?? runStep?.completed_at ?? null,
      durationMs: runStep?.tool_result?.duration_ms ?? null,
      children: runStep ? detailsOf(runStep) : undefined,
    };
  });
  // 段の記録に無い step（古い Run・記録の前の step）は、ツールの段として後ろに並べる。
  for (const step of run.steps) {
    if (used.has(step.id)) continue;
    const status = stepTimelineStatus(step);
    steps.push({
      id: `step:${step.id}`,
      label: toolStepLabel(step, status, titles),
      status,
      startedAt: step.started_at ?? step.tool_result?.started_at ?? null,
      finishedAt: step.completed_at ?? step.tool_result?.completed_at ?? null,
      durationMs: step.tool_result?.duration_ms ?? null,
      children: detailsOf(step),
    });
  }
  return steps;
}
