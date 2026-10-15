import {
  chatProgressStepsFromEvents,
  type RunExecutorFilter as CommonRunExecutorFilter,
  type RunDetailLabelsInput,
  type RunHistoryLabelsInput,
  type RunStatus as CommonRunStatus,
  type RunStatusFilter as CommonRunStatusFilter,
  type RunTimelineStep,
  type RunTimelineStepStatus,
} from "@production-ready/ui";

import type { RunExecutorFilter, RunListKind, RunListStatus, RunState, RunStep } from "@/lib/api";
import { AGENT_CHAT_PROGRESS_STEPS } from "@/lib/chat-progress";
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

/** 種類（きっかけ）の絞り込みの値。先頭の `all` が既定。 */
export const AGENT_RUN_KINDS: readonly RunListKind[] = ["user", "automation", "mcp", "replay", "evaluation"];

export type AgentRunKindFilter = "all" | RunListKind;

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

/** ツールの段（`run.steps`）の名前（チャットの処理の経過と同じ文言）。 */
function toolStepLabel(step: RunStep, status: RunTimelineStepStatus): string {
  const tool = step.tool_call?.name ?? step.kind;
  const progressStatus = status === "waiting" ? "running" : status;
  return AGENT_CHAT_PROGRESS_STEPS.tool.label(progressStatus, { tool });
}

/**
 * 処理の経過の段（`RunTimeline`）。段の名前と状態はチャットの処理の経過（`progress_events` →
 * `AGENT_CHAT_PROGRESS_STEPS`）と同じにし、ツールの段には Run の step（引数・結果・エラー）を中身として結び付ける
 * （同じツールの n 回目の段 ↔ n 回目の step）。段の記録の無い古い Run は step だけで並べる。
 */
export function runTimelineSteps(
  run: RunState,
  detailsOf: (step: RunStep) => RunTimelineStep["children"]
): RunTimelineStep[] {
  const toolSteps = run.steps.filter((step) => step.tool_call);
  const used = new Set<string>();
  const stepFor = (tool: string): RunStep | undefined => {
    const match = toolSteps.find((step) => step.tool_call?.name === tool && !used.has(step.id));
    if (match) used.add(match.id);
    return match;
  };
  const progress = run.progress_events?.length
    ? chatProgressStepsFromEvents(run.progress_events, AGENT_CHAT_PROGRESS_STEPS, run.id)
    : [];
  const steps: RunTimelineStep[] = progress.map((item) => {
    const waiting =
      item.status === "running" &&
      (item.id.startsWith("approval") || item.id.startsWith("confirmation")) &&
      (run.status === "waiting_approval" || run.status === "waiting_confirmation");
    const status: RunTimelineStepStatus = waiting ? "waiting" : item.status;
    const tool = item.id.startsWith("tool:") ? item.id.slice("tool:".length).replace(/#\d+$/, "") : null;
    const runStep = tool ? stepFor(tool) : undefined;
    return {
      id: item.id,
      label: item.label,
      status,
      startedAt: item.startedAt ?? runStep?.started_at ?? null,
      finishedAt: item.finishedAt ?? runStep?.completed_at ?? null,
      durationMs: runStep?.tool_result?.duration_ms ?? null,
      detail: item.detail,
      children: runStep ? detailsOf(runStep) : undefined,
    };
  });
  // 段の記録に無い step（古い Run・記録の前の step）は、ツールの段として後ろに並べる。
  for (const step of run.steps) {
    if (used.has(step.id)) continue;
    const status = stepTimelineStatus(step);
    steps.push({
      id: `step:${step.id}`,
      label: toolStepLabel(step, status),
      status,
      startedAt: step.started_at ?? step.tool_result?.started_at ?? null,
      finishedAt: step.completed_at ?? step.tool_result?.completed_at ?? null,
      durationMs: step.tool_result?.duration_ms ?? null,
      children: detailsOf(step),
    });
  }
  return steps;
}
