/**
 * チャットの回答の処理の段階（3 製品共通の `ChatProgressStep`。#1145 / #1147）を、Run から組み立てる。
 *
 * 新しい配信は作らず、チャットが取り直している Run（状態・ツールの step・承認・イベント）から作る。
 * 段階は「進め方の検討（plan）」→ ツールの呼び出し（`tool:<ツール名>`）→ 承認待ち（`approval_wait`。
 * 承認が要るツールがあったときだけ）→「回答の作成（respond）」。
 */

import type { ChatProgressStep, ChatProgressStepStatus } from "@engchina/production-ready-ui";

import type { ApprovalRequest, RunEvent, RunState, RunStep } from "./api";
import { t } from "./i18n";

type StepKind = "plan" | "tool" | "approval" | "respond";

/** 組み立て中の段階（名前は状態が決まった後に付ける）。 */
interface DraftStep extends Omit<ChatProgressStep, "label"> {
  kind: StepKind;
  tool?: string;
}

const ACTIVE_RUN_STATUSES = new Set<RunState["status"]>(["queued", "running"]);
const FINISHED_TOOL_STATUSES = new Set<RunStep["status"]>(["completed", "failed", "cancelled"]);
const RAN_TOOL_STATUSES = new Set<RunStep["status"]>(["running", "completed", "failed"]);

/** 送った質問（Run の作成の応答の前。#907）の段階。送信が遅いとき、回答の作成ではなく送信で待っていると分かる。 */
export function chatSubmitProgressSteps(sentAtMs: number): ChatProgressStep[] {
  return [
    { id: "submit", label: t("chat.progress.submit.running"), status: "running", startedAt: new Date(sentAtMs).toISOString() },
  ];
}

/** Run の処理の段階。 */
export function runProgressSteps(run: RunState): ChatProgressStep[] {
  const toolSteps = run.steps.filter((step) => step.tool_call);
  const ranTools = toolSteps.filter((step) => RAN_TOOL_STATUSES.has(step.status));
  const pendingApprovals = run.approvals.filter((approval) => approval.status === "pending");
  // 失敗・停止した Run も、その時点で実行中だった段階を決めるために実行中と同じ判定をする（finishSteps が確定する）。
  const inFlight = ACTIVE_RUN_STATUSES.has(run.status) || run.status === "failed" || run.status === "cancelled";
  const startedAt =
    firstEvent(run.events, (event) => event.type === "run.status_changed" && event.payload.status === "running")?.created_at ??
    run.created_at;
  const endedAt = lastEvent(run.events, (event) => ["run.completed", "runtime.failed", "run.cancelled"].includes(event.type))?.created_at;
  // 最初のツールの呼び出し（承認を求めたときは、その時刻）。
  const firstToolAt = earliest([...toolSteps.map((step) => step.started_at), ...run.approvals.map((approval) => approval.created_at)]);
  const lastToolFinishedAt = latest(toolSteps.map((step) => (FINISHED_TOOL_STATUSES.has(step.status) ? step.completed_at : null)));

  // モデルが最初に考えている段階。ツールを呼んだら（承認を求めたら）終わる。
  const planFinished = toolSteps.length > 0 || run.approvals.length > 0;
  const plan = withTimes(
    { id: "plan", kind: "plan", status: planFinished || run.status === "completed" ? "done" : "running" },
    startedAt,
    planFinished ? firstToolAt : run.status === "completed" ? endedAt : undefined
  );

  const tools = toolSteps.map((step, index) => toolProgressStep(step, toolStepId(toolSteps, index), run.approvals));

  // ツールの結果を受け取った後、回答ができるまで（ツールを呼ばずに答えたときは完了だけ出す）。
  const respondRunning =
    inFlight &&
    ranTools.length > 0 &&
    pendingApprovals.length === 0 &&
    toolSteps.every((step) => FINISHED_TOOL_STATUSES.has(step.status));
  const respond = withTimes(
    { id: "respond", kind: "respond", status: run.status === "completed" ? "done" : respondRunning ? "running" : "pending" },
    run.status === "completed" || respondRunning ? lastToolFinishedAt : undefined,
    run.status === "completed" ? endedAt : undefined
  );

  const steps: DraftStep[] = [plan, ...tools];
  if (run.approvals.length > 0) {
    // 承認待ちは、承認が要った最初のツールの前に置く（承認の後にそのツールを実行する）。
    const firstApprovalTool = toolSteps.findIndex((step) => step.approval_id);
    steps.splice(firstApprovalTool >= 0 ? firstApprovalTool + 1 : steps.length, 0, approvalProgressStep(run.approvals, pendingApprovals));
  }
  steps.push(respond);
  return finishSteps(steps, run.status, endedAt).map(({ kind, tool, ...step }) => ({
    ...step,
    label: stepLabel(kind, step.status, tool),
  }));
}

/**
 * 段階の名前（実行中「〜しています」・完了「〜しました」・失敗「〜できませんでした」・未実行は名詞。NL2SQL と同じ。#1145）。
 * i18n の key は静的に書く（辞書の検査が key を見つけられるように）。
 */
function stepLabel(kind: StepKind, status: ChatProgressStepStatus, tool = ""): string {
  const state = status === "running" || status === "done" || status === "failed" ? status : "idle";
  switch (kind) {
    case "plan":
      return {
        running: t("chat.progress.plan.running"),
        done: t("chat.progress.plan.done"),
        failed: t("chat.progress.plan.failed"),
        idle: t("chat.progress.plan.idle"),
      }[state];
    case "tool":
      return {
        running: t("chat.progress.tool.running", { tool }),
        done: t("chat.progress.tool.done", { tool }),
        failed: t("chat.progress.tool.failed", { tool }),
        idle: t("chat.progress.tool.idle", { tool }),
      }[state];
    case "approval":
      return {
        running: t("chat.progress.approval.running"),
        done: t("chat.progress.approval.done"),
        failed: t("chat.progress.approval.failed"),
        idle: t("chat.progress.approval.idle"),
      }[state];
    case "respond":
      return {
        running: t("chat.progress.respond.running"),
        done: t("chat.progress.respond.done"),
        failed: t("chat.progress.respond.failed"),
        idle: t("chat.progress.respond.idle"),
      }[state];
  }
}

/** 同じツールを 2 回以上呼んだときは、2 回目から id に回数を付ける（id は一覧の中で一意）。 */
function toolStepId(toolSteps: RunStep[], index: number): string {
  const name = toolSteps[index].tool_call?.name ?? "";
  const count = toolSteps.slice(0, index + 1).filter((step) => step.tool_call?.name === name).length;
  return count > 1 ? `tool:${name}#${count}` : `tool:${name}`;
}

function toolProgressStep(step: RunStep, id: string, approvals: ApprovalRequest[]): DraftStep {
  const status: ChatProgressStepStatus =
    step.status === "running"
      ? "running"
      : step.status === "completed"
        ? "done"
        : step.status === "failed"
          ? "failed"
          : step.status === "cancelled"
            ? "skipped"
            : "pending";
  const draft: DraftStep = { id, kind: "tool", tool: step.tool_call?.name ?? "", status };
  const approval = step.approval_id ? approvals.find((item) => item.id === step.approval_id) : undefined;
  if (step.status === "cancelled" && approval?.status === "rejected") {
    draft.detail = t("chat.progress.toolRejected");
  }
  // 承認待ち・承認の後の実行待ちのツールは、まだ実行していないので時刻を出さない。
  const ran = RAN_TOOL_STATUSES.has(step.status);
  return withTimes(draft, ran ? step.started_at : undefined, ran ? step.completed_at : undefined);
}

function approvalProgressStep(approvals: ApprovalRequest[], pending: ApprovalRequest[]): DraftStep {
  const draft: DraftStep = { id: "approval_wait", kind: "approval", status: pending.length > 0 ? "running" : "done" };
  if (pending.length > 0) {
    draft.detail = pending.map((approval) => approval.tool_call.name).join("、");
  }
  return withTimes(
    draft,
    earliest(approvals.map((approval) => approval.created_at)),
    pending.length > 0 ? undefined : latest(approvals.map((approval) => approval.decided_at))
  );
}

/** 終わった Run の段階を確定する。失敗は実行中の段階を failed、停止は終わっていない段階を skipped にする。 */
function finishSteps(steps: DraftStep[], status: RunState["status"], endedAt: string | undefined): DraftStep[] {
  if (status === "failed") {
    return steps.map((step) => (step.status === "running" ? withTimes({ ...step, status: "failed" }, step.startedAt, endedAt) : step));
  }
  if (status === "cancelled") {
    return steps.map((step) =>
      step.status === "running" || step.status === "pending"
        ? withTimes({ ...step, status: "skipped" }, step.startedAt, step.status === "running" ? endedAt : undefined)
        : step
    );
  }
  return steps;
}

function withTimes(step: DraftStep, startedAt: string | null | undefined, finishedAt: string | null | undefined): DraftStep {
  const next: DraftStep = { ...step };
  delete next.startedAt;
  delete next.finishedAt;
  if (startedAt) next.startedAt = startedAt;
  if (finishedAt) next.finishedAt = finishedAt;
  return next;
}

function firstEvent(events: RunEvent[], match: (event: RunEvent) => boolean): RunEvent | undefined {
  return events.find(match);
}

function lastEvent(events: RunEvent[], match: (event: RunEvent) => boolean): RunEvent | undefined {
  return [...events].reverse().find(match);
}

function earliest(values: (string | null | undefined)[]): string | undefined {
  return sortedTimes(values)[0];
}

function latest(values: (string | null | undefined)[]): string | undefined {
  return sortedTimes(values).at(-1);
}

function sortedTimes(values: (string | null | undefined)[]): string[] {
  return values
    .filter((value): value is string => typeof value === "string" && !Number.isNaN(Date.parse(value)))
    .sort((a, b) => Date.parse(a) - Date.parse(b));
}
